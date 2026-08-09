// The mobile client: a Guacamole session with no webapp behind it.
//
// Everything the Java client would normally do -- authenticate, list
// connections, pick one -- is gone. There is exactly one session, and opening
// the page is what starts it.

import Guacamole from '/static/vendor/guacamole-common.min.js';
import { attachKeyboard, attachPointer } from '/static/input.js';

const $ = (id) => document.getElementById(id);

const stage = $('stage');
const sink = $('keyboard-sink');
const controls = $('controls');

/**
 * Physical pixels below which the remote session must not be asked to shrink.
 *
 * Firefox will not size its window under 450 CSS px, and inside the session
 * those are multiplied by DEVICE_PIXEL_RATIO -- 450 * 2.4, see
 * containers/firefox/firefox-user.js. Ask for a narrower session and Firefox
 * comes out wider than the screen, clipping the right edge of every page. A
 * phone at 390 CSS px with a dpr of 2 would do exactly that.
 */
const MIN_REMOTE_WIDTH = 1080;

/** Matches `Settings.max_dimension`; the tunnel clamps to it regardless. */
const MAX_REMOTE_DIMENSION = 4096;

/** Quiet period before asking the session to resize. See requestSize(). */
const RESIZE_SETTLE_MS = 250;

/**
 * How long to leave a resize request unanswered before repeating it.
 *
 * Comfortably longer than a resize takes. A resize here is a whole RDP
 * reconnect -- measured at 1.1 to 2.1 seconds -- so a retry timed for the old
 * display-update round trip would fire while the first one was still in
 * flight, and buy a second reconnect for nothing.
 */
const RESIZE_RETRY_MS = 3000;

/** Times to ask before settling for a letterbox. */
const RESIZE_ATTEMPTS = 2;

// The remote geometry, as reported by guacd once the session has actually
// resized. What we asked for is only a request: the round trip is an RDP
// reconnect and a full Firefox reflow, so this always lags and is never
// assumed.
let remote = { width: 0, height: 0 };
let scale = 1;
let client = null;
let tunnel = null;
let lastInputAt = null;

const stats = { opcode: '–', instructions: 0, latency: null, state: 'connecting', input: '–' };

/** Records what the last gesture or keystroke actually sent. */
function noteInput(what) {
  stats.input = what;
  lastInputAt = performance.now();
  wake();
  render();
}

// --- geometry -------------------------------------------------------------
//
// Rotation asks the remote session to change shape. guacd carries that by
// remaking the RDP connection at the new size -- weston takes its geometry from
// capability exchange and offers no channel to change it mid-session -- and
// weston resizes the output, kiosk-shell reconfigures Firefox, Firefox reflows.
// Scaling stays as the fallback: something has to be on screen for the second
// or two that takes, and the request is not always granted in full.

/**
 * The session size this viewport wants, in physical pixels.
 *
 * The dpi is always 96, and that is not laziness. To guacd's RDP client the
 * `size` instruction's dpi is not metadata, it is a divisor: it rescales the
 * pixels you asked for by 96/dpi before handing them to the server. Sending
 * this phone's real 230 asks for 1080x2400 and gets a 560x1252 session. All
 * the scaling this study does is explicit and elsewhere -- devPixelsPerPx
 * inside Firefox, display.scale() out here -- so 96 is how you say "these are
 * the pixels I meant".
 */
function wantedSize() {
  const viewport = window.visualViewport;
  const ratio = window.devicePixelRatio || 1;
  const clamp = (value) =>
    Math.min(Math.round(value * ratio), MAX_REMOTE_DIMENSION);

  return {
    width: Math.max(clamp(viewport ? viewport.width : window.innerWidth), MIN_REMOTE_WIDTH),
    height: clamp(viewport ? viewport.height : window.innerHeight),
    dpi: 96,
  };
}

function fit() {
  if (!client || !remote.width || !remote.height) return;

  // Not while the on-screen keyboard is up. It shrinks the visual viewport,
  // and rescaling the session to the sliver left above the keys makes typing
  // unreadable -- and it happens on every single keypress.
  if (document.activeElement === sink) return;

  const viewport = window.visualViewport;
  const width = viewport ? viewport.width : window.innerWidth;
  const height = viewport ? viewport.height : window.innerHeight;

  scale = Math.min(width / remote.width, height / remote.height);
  client.getDisplay().scale(scale);
  render();
}

/**
 * Ask the remote session to become the shape of this viewport.
 *
 * Repeated once, because the session it is asking is not always ready to be
 * asked. Fewer repeats than the xorgxrdp version needed: that one dropped an
 * early resize in silence, whereas a reconnect either happens or ends the
 * connection loudly. Giving up is safe: fit() letterboxes, which is only what
 * the VNC version always did.
 */
function requestSize() {
  if (!client) return;

  // Same reason fit() bows out: the on-screen keyboard shrinks the visual
  // viewport, and reflowing the *remote session* down to the sliver above the
  // keys -- on every keypress -- is far worse than rescaling ever was.
  if (document.activeElement === sink) return;

  const wanted = wantedSize();
  if (wanted.width === remote.width && wanted.height === remote.height) return;

  client.sendSize(wanted.width, wanted.height);

  if (--attemptsLeft > 0) {
    resizeTimer = setTimeout(requestSize, RESIZE_RETRY_MS);
  }
}

// A phone reports a resize for every keyboard show/hide and every scroll of
// the URL bar. Rescaling is cheap, so it happens once a frame; resizing is not
// -- each request costs an RDP reconnect and a full Firefox reflow, a second or
// two of it -- so it waits for the viewport to hold still first.
let pendingFit = null;
let resizeTimer = null;
let attemptsLeft = 0;
function scheduleGeometry() {
  if (pendingFit === null) {
    pendingFit = requestAnimationFrame(() => {
      pendingFit = null;
      fit();
    });
  }

  attemptsLeft = RESIZE_ATTEMPTS;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(requestSize, RESIZE_SETTLE_MS);
}

// --- the session ----------------------------------------------------------

const STATES = ['idle', 'connecting', 'waiting', 'connected', 'disconnecting', 'disconnected'];

function connect() {
  disconnect();

  // The initial size is the same question requestSize() asks later, so it is
  // the same answer: the session opens at the shape it will keep.
  const query = new URLSearchParams(wantedSize());

  // The tunnel URL must not carry a query string of its own: the library
  // builds the socket URL as `url + "?" + data`, so anything already there
  // produces a second '?' and the parameters are lost.
  tunnel = new Guacamole.WebSocketTunnel('tunnel');
  client = new Guacamole.Client(tunnel);

  // Guacamole.Client installs its own tunnel.oninstruction in its constructor,
  // and that handler is what draws every frame. Replacing it silently disables
  // rendering, so chain onto it rather than assigning over it.
  const draw = tunnel.oninstruction;
  tunnel.oninstruction = (opcode, args) => {
    stats.opcode = opcode || '(tunnel)';
    stats.instructions += 1;
    if (opcode === 'sync' && lastInputAt !== null) {
      stats.latency = Math.round(performance.now() - lastInputAt);
      lastInputAt = null;
    }
    draw(opcode, args);
  };

  client.onstatechange = (state) => {
    stats.state = STATES[state] ?? String(state);
    render();
  };

  client.onerror = (status) => {
    stats.state = `error: ${status.message || status.code}`;
    render();
  };

  const display = client.getDisplay();
  display.onresize = (width, height) => {
    remote = { width, height };
    fit();
  };

  const element = display.getElement();
  stage.replaceChildren(element);
  attachPointer({ element, client: () => client, onInput: noteInput });

  client.connect(query.toString());

  // A phone locking its screen suspends the page; leaving the session open
  // means guacd keeps rendering into a socket nobody is reading.
  window.addEventListener('unload', () => client?.disconnect());
}

function disconnect() {
  try {
    client?.disconnect();
  } catch {
    // Already gone. Nothing to do.
  }
  client = null;
  tunnel = null;
}

// --- chrome ---------------------------------------------------------------

let idleTimer = null;
function wake() {
  controls.dataset.idle = 'false';
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    controls.dataset.idle = 'true';
  }, 2500);
}

function render() {
  const connected = stats.state === 'connected';
  $('dot').dataset.state = connected ? 'connected' : stats.state;
  $('summary').textContent = connected
    ? `${remote.width}×${remote.height} · ${(scale * 100).toFixed(0)}%` +
      (stats.latency === null ? '' : ` · ${stats.latency}ms`)
    : stats.state;

  $('d-state').textContent = stats.state;
  $('d-remote').textContent = remote.width ? `${remote.width}×${remote.height}` : '–';
  $('d-viewport').textContent =
    `${Math.round(window.innerWidth)}×${Math.round(window.innerHeight)} @${window.devicePixelRatio}x`;
  $('d-scale').textContent = `${(scale * 100).toFixed(1)}%`;
  $('d-latency').textContent = stats.latency === null ? '–' : `${stats.latency}ms`;
  $('d-input').textContent = stats.input;
  $('d-opcode').textContent = stats.opcode;
  $('d-instructions').textContent = String(stats.instructions);
}

$('strip').addEventListener('click', (event) => {
  event.stopPropagation();
  const detail = $('detail');
  detail.hidden = !detail.hidden;
  $('strip').setAttribute('aria-expanded', String(!detail.hidden));
});

$('keyboard').addEventListener('click', () => {
  // Must happen inside the tap handler: browsers only raise the keyboard on a
  // focus that a user gesture caused.
  if (document.activeElement === sink) sink.blur();
  else sink.focus();
});

$('fullscreen').addEventListener('click', async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen({ navigationUI: 'hide' }).catch(() => {});
  scheduleGeometry();
});

$('reconnect').addEventListener('click', connect);

for (const event of ['pointerdown', 'touchstart']) {
  document.addEventListener(event, wake, { passive: true });
}

window.addEventListener('resize', scheduleGeometry);
window.addEventListener('orientationchange', scheduleGeometry);
window.visualViewport?.addEventListener('resize', scheduleGeometry);
sink.addEventListener('blur', scheduleGeometry);

attachKeyboard({ sink, client: () => client, onInput: noteInput });

// Keystrokes only reach the session while the capture field holds focus, so
// on a desktop clicking the display has to hand it over. Not on a phone: the
// on-screen keyboard would then cover the screen on every single tap, which
// is what the keyboard button is for.
if (!window.matchMedia('(pointer: coarse)').matches) {
  stage.addEventListener('mousedown', () => sink.focus());
}

setInterval(render, 500);
wake();
connect();

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

// The remote geometry, as reported by guacd once VNC tells it the truth. Our
// requested size is only a hint: Xvnc is a fixed framebuffer and does not
// resize (see the README), so this is what we scale to fit.
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

// --- scaling --------------------------------------------------------------
//
// Rotation cannot reflow the remote session, so it rescales instead: the
// display is fitted into the viewport, letterboxed, and stays connected.

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

// A phone reports a resize for every keyboard show/hide and every scroll of
// the URL bar, so coalesce them into one rescale per frame.
let pendingFit = null;
function scheduleFit() {
  if (pendingFit !== null) return;
  pendingFit = requestAnimationFrame(() => {
    pendingFit = null;
    fit();
  });
}

// --- the session ----------------------------------------------------------

const STATES = ['idle', 'connecting', 'waiting', 'connected', 'disconnecting', 'disconnected'];

function connect() {
  disconnect();

  const viewport = window.visualViewport;
  const ratio = window.devicePixelRatio || 1;
  const query = new URLSearchParams({
    width: Math.round((viewport ? viewport.width : window.innerWidth) * ratio),
    height: Math.round((viewport ? viewport.height : window.innerHeight) * ratio),
    dpi: Math.round(96 * ratio),
  });

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
  scheduleFit();
});

$('reconnect').addEventListener('click', connect);

for (const event of ['pointerdown', 'touchstart']) {
  document.addEventListener(event, wake, { passive: true });
}

window.addEventListener('resize', scheduleFit);
window.addEventListener('orientationchange', scheduleFit);
window.visualViewport?.addEventListener('resize', scheduleFit);
sink.addEventListener('blur', scheduleFit);

attachKeyboard({ sink, client: () => client, onInput: noteInput });

setInterval(render, 500);
wake();
connect();

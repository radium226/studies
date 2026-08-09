// The mobile client: a Guacamole session with no webapp behind it.
//
// Everything the Java client would normally do -- authenticate, list
// connections, pick one -- is gone. There is exactly one session, and opening
// the page is what starts it.

import Guacamole from '/static/vendor/guacamole-common.min.js';

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

const stats = { opcode: '–', instructions: 0, latency: null, state: 'connecting' };

// --- scaling --------------------------------------------------------------
//
// Rotation cannot reflow the remote session, so it rescales instead: the
// display is fitted into the viewport, letterboxed, and stays connected.

function fit() {
  if (!client || !remote.width || !remote.height) return;

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

// --- input ----------------------------------------------------------------

function markInput() {
  lastInputAt = performance.now();
  wake();
}

function attachPointer(element) {
  const touch = new Guacamole.Mouse.Touchscreen(element);
  const mouse = new Guacamole.Mouse(element);

  const send = (state) => {
    if (!client) return;
    markInput();
    // Touch coordinates arrive in on-screen pixels; the remote session is in
    // its own, unscaled ones.
    client.sendMouseState(
      new Guacamole.Mouse.State(
        state.x / scale,
        state.y / scale,
        state.left,
        state.middle,
        state.right,
        state.up,
        state.down,
      ),
    );
  };

  for (const source of [touch, mouse]) {
    source.onmousedown = source.onmouseup = source.onmousemove = send;
  }
}

function attachKeyboard() {
  const keyboard = new Guacamole.Keyboard(document);
  keyboard.onkeydown = (keysym) => {
    markInput();
    client?.sendKeyEvent(1, keysym);
  };
  keyboard.onkeyup = (keysym) => {
    markInput();
    client?.sendKeyEvent(0, keysym);
  };

  // Phone keyboards mostly do not produce usable key events -- they report
  // keyCode 229 and commit text instead. So take the committed text and
  // synthesise key presses from it.
  sink.addEventListener('beforeinput', (event) => {
    markInput();
    if (event.inputType === 'deleteContentBackward') return pressKeysym(0xff08);
    if (event.inputType === 'insertLineBreak') return pressKeysym(0xff0d);
    for (const character of event.data ?? '') pressKeysym(keysymOf(character));
  });

  // Never let it accumulate text: it exists to capture keystrokes, not to hold
  // a value.
  sink.addEventListener('input', () => {
    sink.value = '';
  });
}

// X11 keysyms are Latin-1 directly, and everything else is the codepoint with
// the Unicode plane flag set.
function keysymOf(character) {
  const codepoint = character.codePointAt(0);
  return codepoint <= 0xff ? codepoint : 0x01000000 | codepoint;
}

function pressKeysym(keysym) {
  client?.sendKeyEvent(1, keysym);
  client?.sendKeyEvent(0, keysym);
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
  attachPointer(element);

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

setInterval(render, 500);
wake();
connect();

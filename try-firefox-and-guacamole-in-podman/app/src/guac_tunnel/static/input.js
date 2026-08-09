// Turning phone gestures into the mouse and keyboard a VNC session expects.
//
// The remote end has no idea a touchscreen exists: guacd speaks VNC, and VNC
// speaks mouse. Everything here is about that mismatch.

import Guacamole from '/static/vendor/guacamole-common.min.js';

/** Movement beyond this (CSS px) means a drag rather than a tap. */
const DRAG_THRESHOLD = 10;

/** Hold longer than this (ms) without moving and it is a right click. */
const LONG_PRESS_MS = 500;

/** CSS px of drag per notch of scroll wheel. */
const PIXELS_PER_NOTCH = 40;

/**
 * Where a viewport point lands on the display, in the display's own units.
 *
 * Deliberately the same call `Guacamole.Mouse` makes internally, rather than
 * bounding-box arithmetic of our own: touch and mouse then agree by
 * construction, and both are sent with `applyDisplayScale` so the client
 * divides by the current scale factor. One coordinate path, not two.
 */
function positionOf(element, clientX, clientY) {
  return Guacamole.Position.fromClientPosition(element, clientX, clientY);
}

/**
 * Binds touch and mouse input to a Guacamole client.
 *
 * @param {object} options
 * @param {Element} options.element  the display element to listen on
 * @param {() => object} options.client  the live client, or null
 * @param {(what: string) => void} options.onInput  for the debug strip
 */
export function attachPointer({ element, client, onInput }) {
  // `true` is what makes the coordinates remote ones: the client divides by
  // the display scale itself. Both paths below rely on it.
  const send = (state) => {
    const target = client();
    if (target) target.sendMouseState(state, true);
  };

  const at = (point, buttons = {}) =>
    send(new Guacamole.Mouse.State({ x: point.x, y: point.y, ...buttons }));

  const clickAt = (point, button) => {
    at(point, { [button]: true });
    at(point, { [button]: false });
  };

  const scrollAt = (point, notches, direction) => {
    for (let n = 0; n < notches; n++) clickAt(point, direction);
  };

  // --- touch --------------------------------------------------------------

  let gesture = null;

  const pointOf = (touch) => positionOf(element, touch.clientX, touch.clientY);

  element.addEventListener(
    'touchstart',
    (event) => {
      // Also stops the browser synthesising mouse events from this touch,
      // which would otherwise arrive as a second, conflicting click.
      event.preventDefault();

      const touch = event.touches[0];
      const point = pointOf(touch);

      gesture = {
        startX: touch.clientX,
        startY: touch.clientY,
        lastY: touch.clientY,
        startedAt: performance.now(),
        point,
        moved: false,
        scrolled: 0,
        fingers: event.touches.length,
      };
      onInput(`touch ${Math.round(point.x)},${Math.round(point.y)}`);
    },
    { passive: false },
  );

  element.addEventListener(
    'touchmove',
    (event) => {
      event.preventDefault();
      if (!gesture) return;

      const touch = event.touches[0];
      gesture.fingers = Math.max(gesture.fingers, event.touches.length);

      if (Math.hypot(touch.clientX - gesture.startX, touch.clientY - gesture.startY) > DRAG_THRESHOLD) {
        gesture.moved = true;
      }
      if (!gesture.moved) return;

      // A drag scrolls rather than drags. The remote sees a mouse, and a mouse
      // drag means "select text" -- so dragging a page would smear a selection
      // across it instead of scrolling, which is the opposite of what a finger
      // on a phone is asking for.
      const travelled = gesture.lastY - touch.clientY;
      const notches = Math.trunc(Math.abs(travelled) / PIXELS_PER_NOTCH);
      if (notches > 0) {
        const point = pointOf(touch);
        scrollAt(point, notches, travelled > 0 ? 'down' : 'up');
        gesture.lastY -= Math.sign(travelled) * notches * PIXELS_PER_NOTCH;
        gesture.scrolled += notches;
        onInput(`scroll ${travelled > 0 ? 'down' : 'up'} ×${gesture.scrolled}`);
      }
    },
    { passive: false },
  );

  const endTouch = (event) => {
    event.preventDefault();
    if (!gesture) return;

    const held = performance.now() - gesture.startedAt;
    const { point, moved, fingers } = gesture;
    gesture = null;

    if (moved) return; // already handled as scrolling

    if (fingers > 1) {
      clickAt(point, 'right');
      onInput('right click (two fingers)');
    } else if (held >= LONG_PRESS_MS) {
      clickAt(point, 'right');
      onInput('right click (long press)');
    } else {
      // Move first: the remote needs the pointer under the target before the
      // button goes down, or the click lands wherever the pointer last was.
      at(point);
      clickAt(point, 'left');
      onInput(`click ${Math.round(point.x)},${Math.round(point.y)}`);
    }
  };

  element.addEventListener('touchend', endTouch, { passive: false });
  element.addEventListener('touchcancel', endTouch, { passive: false });

  // --- mouse, for when this is opened on a desktop -------------------------

  const mouse = new Guacamole.Mouse(element);
  mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = send;
}

/**
 * Binds keyboard input.
 *
 * Phone keyboards are not keyboards: they mostly report keyCode 229 and commit
 * text instead, so key events have to be reconstructed from what was typed.
 *
 * @param {object} options
 * @param {HTMLTextAreaElement} options.sink  focused to raise the keyboard
 * @param {() => object} options.client
 * @param {(what: string) => void} options.onInput
 */
export function attachKeyboard({ sink, client, onInput }) {
  // Anything to the left of the caret will do; it exists so that backspace has
  // something to delete. Without it the field is empty, browsers report
  // nothing to delete, and `beforeinput` never fires -- backspace silently
  // does nothing at all.
  const FILLER = '​'.repeat(32);

  const reset = () => {
    sink.value = FILLER;
    sink.setSelectionRange(FILLER.length, FILLER.length);
  };

  const press = (keysym, label) => {
    const target = client();
    if (!target) return;
    target.sendKeyEvent(1, keysym);
    target.sendKeyEvent(0, keysym);
    onInput(`key ${label}`);
  };

  // X11 keysyms are Latin-1 directly; everything else is the codepoint with
  // the Unicode plane flag set.
  const keysymOf = (character) => {
    const codepoint = character.codePointAt(0);
    return codepoint <= 0xff ? codepoint : 0x01000000 | codepoint;
  };

  sink.addEventListener('beforeinput', (event) => {
    switch (event.inputType) {
      case 'deleteContentBackward':
        press(0xff08, 'BackSpace');
        break;
      case 'deleteContentForward':
        press(0xffff, 'Delete');
        break;
      case 'insertLineBreak':
      case 'insertParagraph':
        press(0xff0d, 'Return');
        break;
      default:
        for (const character of event.data ?? '') press(keysymOf(character), character);
    }
  });

  // Never let the field accumulate what was typed: it is a keystroke capture,
  // not somewhere text lives.
  sink.addEventListener('input', reset);
  sink.addEventListener('focus', reset);

  // A real keyboard, for when this is opened on a desktop. Only while the sink
  // is *not* focused: with it focused both paths fire and every character is
  // sent twice.
  const keyboard = new Guacamole.Keyboard(document);
  keyboard.onkeydown = (keysym) => {
    if (document.activeElement === sink) return;
    client()?.sendKeyEvent(1, keysym);
    onInput(`key ${keysym}`);
  };
  keyboard.onkeyup = (keysym) => {
    if (document.activeElement === sink) return;
    client()?.sendKeyEvent(0, keysym);
  };

  reset();
}

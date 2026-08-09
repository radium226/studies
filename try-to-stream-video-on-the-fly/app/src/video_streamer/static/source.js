const form = document.getElementById("source-form");
const input = document.getElementById("source-url");
const loopCheckbox = document.getElementById("source-loop");
const syntheticButton = document.getElementById("source-synthetic");
const stopButton = document.getElementById("source-stop");
const errorBox = document.getElementById("source-error");
const errorText = document.getElementById("source-error-text");

// The status line is shared with player.js, which overwrites it on every
// reconnect/idle poll — a failed source closes the broadcaster, so the idle
// "No source, waiting for URL" text would clobber the error within seconds.
// Failure details therefore live in this dedicated panel, which persists until
// dismissed or the next source attempt. Exposed on window so player.js can
// surface async mid-stream failures through the same popup.
function showSourceError(title, message) {
  errorText.textContent = `${title}\n\n${message}`;
  errorBox.hidden = false;
}
window.showSourceError = showSourceError;

document.getElementById("source-error-close").addEventListener("click", () => {
  errorBox.hidden = true;
});

// POST a source request (url or synthetic) and report the outcome. Returns
// nothing; player.js's poll loop performs the actual stream handoff.
async function startSource(body, label) {
  const buttons = form.querySelectorAll("button");
  setStatus("starting source...");
  errorBox.hidden = true;
  buttons.forEach((b) => (b.disabled = true));
  try {
    const res = await fetch("/api/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data;
    try {
      data = await res.json();
    } catch {
      data = { error: `unexpected response (HTTP ${res.status} ${res.statusText})` };
    }
    if (!res.ok) throw new Error(data.error || res.statusText);
    setStatus(`source started (${data.width}x${data.height} @ ${data.fps.toFixed(1)}fps)`);
  } catch (err) {
    setStatus("source failed");
    showSourceError(label, err.message);
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const url = input.value.trim();
  if (!url) return;
  startSource({ url, loop: loopCheckbox.checked }, url);
});

syntheticButton.addEventListener("click", () => {
  startSource({ synthetic: true, loop: loopCheckbox.checked }, "test pattern");
});

stopButton.addEventListener("click", async () => {
  setStatus("stopping...");
  try {
    await fetch("/api/stop", { method: "POST" });
  } catch (err) {
    console.error("stop failed", err);
  }
});

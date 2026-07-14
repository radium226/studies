const form = document.getElementById("source-form");
const input = document.getElementById("source-url");
const errorBox = document.getElementById("source-error");
const errorText = document.getElementById("source-error-text");

// The status line is shared with player.js, which overwrites it on every
// reconnect poll — a failed switch closes the broadcaster, so "stream ended -
// waiting for a source..." would clobber the error within seconds. Failure
// details therefore live in this dedicated panel, which persists until
// dismissed or the next switch attempt.
function showSourceError(url, message) {
  errorText.textContent = `${url}\n\n${message}`;
  errorBox.hidden = false;
}

document.getElementById("source-error-close").addEventListener("click", () => {
  errorBox.hidden = true;
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = input.value.trim();
  if (!url) return;

  const button = form.querySelector("button");
  setStatus("switching source...");
  errorBox.hidden = true;
  button.disabled = true;
  try {
    const res = await fetch("/api/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    let data;
    try {
      data = await res.json();
    } catch {
      data = { error: `unexpected response (HTTP ${res.status} ${res.statusText})` };
    }
    if (!res.ok) throw new Error(data.error || res.statusText);
    setStatus(`source switched (${data.width}x${data.height} @ ${data.fps.toFixed(1)}fps)`);
  } catch (err) {
    setStatus("switch failed");
    showSourceError(url, err.message);
  } finally {
    button.disabled = false;
  }
});

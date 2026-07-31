const form = document.getElementById("source-form");
const selectedFileLabel = document.getElementById("selected-file");
const browseButton = document.getElementById("source-browse");
const stopButton = document.getElementById("source-stop");
const errorBox = document.getElementById("source-error");
const errorText = document.getElementById("source-error-text");

const sourceKindFile = document.getElementById("source-kind-file");
const sourceKindUrl = document.getElementById("source-kind-url");
const sourceKindFileRow = document.getElementById("source-kind-file-row");
const sourceKindUrlRow = document.getElementById("source-kind-url-row");
const sourceUrlInput = document.getElementById("source-url");

function updateSourceKindRows() {
  const useUrl = sourceKindUrl.checked;
  sourceKindFileRow.hidden = useUrl;
  sourceKindUrlRow.hidden = !useUrl;
}
sourceKindFile.addEventListener("change", updateSourceKindRows);
sourceKindUrl.addEventListener("change", updateSourceKindRows);

const speedFactorInput = document.getElementById("speed-factor");
const afterFrameCountEnabled = document.getElementById("stop-after-frame-count-enabled");
const afterFrameCountMaxFrames = document.getElementById("stop-after-frame-count-max-frames");
const onFirstTrackEnabled = document.getElementById("stop-on-first-track-enabled");
const onFirstTrackMinFrames = document.getElementById("stop-on-first-track-min-frames");

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

// The path of whichever file was last picked from the browse modal — this is what gets
// submitted, not anything typed into the form directly.
let selectedPath = null;

browseButton.addEventListener("click", () => {
  window.openBrowseModal((path) => {
    selectedPath = path;
    selectedFileLabel.textContent = path;
  });
});

// POST a source request and report the outcome. Returns nothing; player.js's
// poll loop performs the actual stream handoff.
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
  const useUrl = sourceKindUrl.checked;
  const sourceField = useUrl
    ? { url: sourceUrlInput.value.trim() }
    : { path: selectedPath };
  const label = useUrl ? sourceUrlInput.value.trim() : selectedPath;
  if (useUrl ? !sourceField.url : !selectedPath) return;
  startSource(
    {
      ...sourceField,
      speed_factor: parseFloat(speedFactorInput.value),
      stop_strategy: {
        after_frame_count: {
          enabled: afterFrameCountEnabled.checked,
          max_frames: parseInt(afterFrameCountMaxFrames.value, 10),
        },
        on_first_track: {
          enabled: onFirstTrackEnabled.checked,
          min_track_frames: parseInt(onFirstTrackMinFrames.value, 10),
        },
      },
    },
    label
  );
});

stopButton.addEventListener("click", async () => {
  setStatus("stopping...");
  try {
    await fetch("/api/stop", { method: "POST" });
  } catch (err) {
    console.error("stop failed", err);
  }
});

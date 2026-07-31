function setStatus(text) {
  const el = document.getElementById("status");
  if (el) el.textContent = text;
}

const video = document.getElementById("player");
const progressBar = document.getElementById("progress-bar");

// Progress bar visibility is the OR of two independent reasons to be
// loading: the connect/reconnect phase (no stream hooked up yet) and the
// video element itself stalling for data mid-stream (e.g. from simulated
// input lag). Tracked separately since they can overlap or occur
// independently.
let loadingPhase = true;
let starvedForData = false;

function updateProgressBar() {
  if (progressBar) progressBar.classList.toggle("visible", loadingPhase || starvedForData);
}

video.addEventListener("waiting", () => {
  starvedForData = true;
  updateProgressBar();
});
video.addEventListener("playing", () => {
  starvedForData = false;
  updateProgressBar();
});

// Each failure record from /api/status carries a monotonic id; track the last
// one we surfaced so a sticky error is shown as a popup exactly once.
let lastShownErrorId = 0;

// How often to re-poll /api/status while idle, waiting for a source.
const IDLE_POLL_MS = 2000;

function maybeShowError(error) {
  if (error && error.id > lastShownErrorId) {
    lastShownErrorId = error.id;
    if (window.showSourceError) {
      window.showSourceError(error.source || "source", error.message || "unknown error");
    }
  }
}

// Detach any media so the <video> stops showing the last decoded frame and
// falls back to its black background. attachLiveStream revokes its own object
// URL when a connection ends, so there's nothing left to release here.
function clearVideo() {
  if (video.hasAttribute("src")) {
    video.removeAttribute("src");
    try { video.load(); } catch { /* resetting an empty element */ }
  }
}

// Idle: no source. Blank the frame and hide the progress bar (no loading
// spinner while we're just waiting), then show the given status text.
function showIdle(text) {
  loadingPhase = false;
  starvedForData = false;
  updateProgressBar();
  clearVideo();
  setStatus(text);
}

// Entry point / reconnect target: ask the server whether a source is playing.
// Idle -> show the waiting state and poll again; playing -> hook up MSE.
async function start() {
  if (!mseSupported()) {
    setStatus("MediaSource + H.264 baseline not supported in this browser.");
    return;
  }

  let status;
  try {
    const res = await fetch("/api/status");
    status = await res.json();
  } catch (err) {
    // Server momentarily unreachable (e.g. mid-rebuild); stay idle and retry.
    setTimeout(start, IDLE_POLL_MS);
    return;
  }

  maybeShowError(status.error);

  if (status.state !== "playing" || !status.stream_url) {
    // No source: keep the bar hidden and the frame blank while we poll.
    showIdle("No source, waiting for URL");
    setTimeout(start, IDLE_POLL_MS);
    return;
  }

  loadingPhase = true;
  updateProgressBar();
  setStatus("connecting...");
  play(status.stream_url);
}

// Stream the given live fMP4 URL until it ends or errors, then fall back to start()'s idle poll.
function play(streamUrl) {
  attachLiveStream(video, streamUrl, {
    onStreaming: () => {
      setStatus("live");
      loadingPhase = false;
      updateProgressBar();
    },
    onEnded: (gone) => {
      if (gone) {
        // The source finished, failed, or was stopped, and we're now idle. Blank the frame right
        // away, then fall back to the status poll (which shows "waiting" or a failure popup).
        showIdle("stream ended - waiting for a source...");
        setTimeout(start, IDLE_POLL_MS);
      } else {
        // Dropped mid-stream (network hiccup, idle timeout, ...): re-poll status rather than
        // assuming idle - it might just be a blip.
        setStatus("reconnecting...");
        loadingPhase = true;
        updateProgressBar();
        setTimeout(start, 1000);
      }
    },
  });
}

start();

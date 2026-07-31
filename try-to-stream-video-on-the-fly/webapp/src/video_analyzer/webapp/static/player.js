const MIME = 'video/mp4; codecs="avc1.42001e"';

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

let currentObjectUrl = null;
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
// falls back to its black background.
function clearVideo() {
  if (currentObjectUrl) {
    URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = null;
  }
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
  if (!window.MediaSource || !MediaSource.isTypeSupported(MIME)) {
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

// Build a fresh MediaSource and stream the given live fMP4 URL until it ends
// or errors, then fall back to start()'s idle poll.
function play(streamUrl) {
  // Each (re)connect gets a fresh MediaSource; release the previous one's
  // object URL so reconnects don't leak blob references.
  if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
  const mediaSource = new MediaSource();
  currentObjectUrl = URL.createObjectURL(mediaSource);
  video.src = currentObjectUrl;

  mediaSource.addEventListener("sourceopen", async () => {
    const sourceBuffer = mediaSource.addSourceBuffer(MIME);
    const queue = [];
    let appending = false;
    let joinedLive = false;

    function bufferedSpan() {
      if (sourceBuffer.buffered.length === 0) return null;
      return {
        start: sourceBuffer.buffered.start(0),
        end: sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1),
      };
    }

    function pump() {
      if (appending || queue.length === 0) return;
      const chunk = queue.shift();
      appending = true;
      try {
        sourceBuffer.appendBuffer(chunk);
      } catch (err) {
        if (err.name === "QuotaExceededError") {
          // Buffer full: put the chunk back, evict the older half, and let
          // the remove()'s updateend reset `appending` and re-run pump.
          queue.unshift(chunk);
          const span = bufferedSpan();
          if (span) {
            try {
              sourceBuffer.remove(span.start, span.start + (span.end - span.start) / 2);
              return;
            } catch (removeErr) {
              console.error("quota eviction failed", removeErr);
            }
          }
        } else {
          console.error("appendBuffer failed", err);
        }
        appending = false;
      }
    }

    sourceBuffer.addEventListener("updateend", () => {
      appending = false;
      const span = bufferedSpan();

      // The encoder's PTS clock runs continuously from server start, not from
      // when this client connected - it can already be far past zero. Seek
      // into the buffered range once so playback actually starts at the live
      // edge instead of stalling forever at currentTime 0.
      if (!joinedLive && span) {
        joinedLive = true;
        video.currentTime = Math.max(span.start, span.end - 0.1);
        video.play().catch(() => {});
      }

      // Trim old buffered data so a long-running session doesn't grow
      // unbounded. Compare buffered *duration*, not the absolute end
      // timestamp (which is unbounded for a long-running live stream).
      if (span && span.end - span.start > 60) {
        try {
          sourceBuffer.remove(span.start, span.end - 30);
          return; // remove() also fires 'updateend'; pump() runs on that follow-up event.
        } catch (err) {
          console.error("buffer trim failed", err);
        }
      }
      pump();
    });

    sourceBuffer.addEventListener("error", (e) => console.error("SourceBuffer error", e));

    // Don't rely solely on the server ending the response promptly when its
    // broadcaster closes (e.g. on a source switch) - if that signal is ever
    // slow or lost in transit, abort and reconnect ourselves rather than
    // leaving the tab stuck until a manual page reload.
    const abortController = new AbortController();
    const IDLE_TIMEOUT_MS = 8000;

    try {
      const response = await fetch(streamUrl, { signal: abortController.signal });
      if (!response.ok) {
        // 410: the stream ended server-side - the source finished, failed, or
        // was stopped, and we're now idle. Blank the frame right away, then
        // fall back to the status poll (which shows "waiting" or a failure popup).
        if (mediaSource.readyState === "open") {
          try { mediaSource.endOfStream(); } catch { /* already ending */ }
        }
        showIdle("stream ended - waiting for a source...");
        setTimeout(start, IDLE_POLL_MS);
        return;
      }
      const reader = response.body.getReader();
      setStatus("live");
      loadingPhase = false;
      updateProgressBar();
      while (true) {
        const idleTimer = setTimeout(() => abortController.abort(), IDLE_TIMEOUT_MS);
        let done, value;
        try {
          ({ done, value } = await reader.read());
        } finally {
          clearTimeout(idleTimer);
        }
        if (done) break;
        queue.push(value);
        pump();
      }
    } catch (err) {
      console.error("stream fetch failed", err);
    }
    // Stream ended or dropped: re-poll status (idle -> waiting, or a new source).
    setStatus("reconnecting...");
    loadingPhase = true;
    updateProgressBar();
    setTimeout(start, 1000);
  });
}

start();

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

async function start() {
  loadingPhase = true;
  updateProgressBar();

  if (!window.MediaSource || !MediaSource.isTypeSupported(MIME)) {
    setStatus("MediaSource + H.264 baseline not supported in this browser.");
    return;
  }

  const mediaSource = new MediaSource();
  video.src = URL.createObjectURL(mediaSource);

  mediaSource.addEventListener("sourceopen", async () => {
    const sourceBuffer = mediaSource.addSourceBuffer(MIME);
    const queue = [];
    let appending = false;
    let joinedLive = false;

    function pump() {
      if (appending || queue.length === 0) return;
      appending = true;
      sourceBuffer.appendBuffer(queue.shift());
    }

    function bufferedSpan() {
      if (sourceBuffer.buffered.length === 0) return null;
      return {
        start: sourceBuffer.buffered.start(0),
        end: sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1),
      };
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
        sourceBuffer.remove(span.start, span.end - 30);
        return; // remove() also fires 'updateend'; pump() runs on that follow-up event.
      }
      pump();
    });

    sourceBuffer.addEventListener("error", (e) => console.error("SourceBuffer error", e));

    try {
      setStatus("connecting...");
      const response = await fetch("/stream.mp4");
      const reader = response.body.getReader();
      setStatus("live");
      loadingPhase = false;
      updateProgressBar();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        queue.push(value);
        pump();
      }
    } catch (err) {
      console.error("stream fetch failed", err);
    } finally {
      setStatus("reconnecting...");
      loadingPhase = true;
      updateProgressBar();
      setTimeout(start, 1000);
    }
  });
}

start();

// Shared MediaSource/SourceBuffer live-tail plumbing: every stream route in this app (the main
// player, every per-track face-loop video) follows the same "init segment + latest fragment, then
// live tail; 410/EOF means it's over" contract, so the buffering/quota/live-edge-seek logic only
// needs to exist once. Loaded as a plain classic script (no bundler here), so MSE_MIME/
// attachLiveStream are ordinary top-level bindings shared with whatever script tags follow it.

const MSE_MIME = 'video/mp4; codecs="avc1.42001e"';

function mseSupported() {
  return !!window.MediaSource && MediaSource.isTypeSupported(MSE_MIME);
}

// Attach a fresh MediaSource to `videoEl` and live-tail `streamUrl` until the server ends it
// (410, or the encoder's own EOF) or the fetch itself fails. One-shot: call it again (it assigns
// a brand new `videoEl.src`) to reconnect.
//
// onStreaming() fires once the HTTP connection to `streamUrl` succeeds, before any data has
// necessarily been decoded yet. onEnded(gone) fires exactly once when the connection concludes -
// `gone` is true for a clean 410/EOF close, false for a network/fetch failure. Both are optional;
// callers that don't need reconnect/status logic (a face-loop thumbnail) can omit them.
function attachLiveStream(videoEl, streamUrl, { onStreaming, onEnded } = {}) {
  const mediaSource = new MediaSource();
  const objectUrl = URL.createObjectURL(mediaSource);
  videoEl.src = objectUrl;

  mediaSource.addEventListener("sourceopen", async () => {
    const sourceBuffer = mediaSource.addSourceBuffer(MSE_MIME);
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
          // Buffer full: put the chunk back, evict the older half, and let the remove()'s
          // updateend reset `appending` and re-run pump.
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

      // The encoder's PTS clock runs continuously from server start, not from when this client
      // connected - it can already be far past zero. Seek into the buffered range once so
      // playback actually starts at the live edge instead of stalling forever at currentTime 0.
      if (!joinedLive && span) {
        joinedLive = true;
        videoEl.currentTime = Math.max(span.start, span.end - 0.1);
        videoEl.play().catch(() => {});
      }

      // Trim old buffered data so a long-running session doesn't grow unbounded. Compare
      // buffered *duration*, not the absolute end timestamp (unbounded for a long-running live
      // stream).
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

    // Don't rely solely on the server ending the response promptly when its broadcaster closes
    // (e.g. on a source switch) - if that signal is ever slow or lost in transit, abort and let
    // the caller reconnect rather than leaving this element stuck until a manual reload.
    const abortController = new AbortController();
    const IDLE_TIMEOUT_MS = 8000;
    let gone = false;

    try {
      const response = await fetch(streamUrl, { signal: abortController.signal });
      if (!response.ok) {
        // The stream ended server-side - the source finished, failed, was stopped, or (for a
        // per-track stream) the pipeline was torn down.
        if (mediaSource.readyState === "open") {
          try { mediaSource.endOfStream(); } catch { /* already ending */ }
        }
        gone = true;
      } else {
        if (onStreaming) onStreaming();
        const reader = response.body.getReader();
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
      }
    } catch (err) {
      console.error("stream fetch failed", err);
    }

    URL.revokeObjectURL(objectUrl);
    if (onEnded) onEnded(gone);
  });
}

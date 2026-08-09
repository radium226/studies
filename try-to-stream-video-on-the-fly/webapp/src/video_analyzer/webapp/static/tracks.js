// Left-hand face-loop column: one small <video> per tracked face, added as /ws/tracks announces
// each track id and live-tailed via the same attachLiveStream() the main player uses.

const tracksColumn = document.getElementById("tracks-column");

// track_id -> <video> already added to the column, so "existing" backfill and live "new_track"
// events (which can race on reconnect) never create a duplicate entry.
const trackEntries = new Map();

async function addTrackEntry(trackId) {
  if (trackEntries.has(trackId) || !tracksColumn) return;
  const video = document.createElement("video");
  video.className = "track-entry";
  video.muted = true;
  video.autoplay = true;
  video.playsInline = true;
  trackEntries.set(trackId, video);
  tracksColumn.appendChild(video);

  try {
    const res = await fetch(`/api/tracks/${trackId}`);
    if (!res.ok) return;
    const { video_url } = await res.json();
    attachLiveStream(video, video_url);
  } catch (err) {
    console.error("failed to load track", trackId, err);
  }
}

function clearTracks() {
  trackEntries.forEach((video) => video.remove());
  trackEntries.clear();
}

// How long to wait before reconnecting /ws/tracks after it closes (idle, or a pipeline
// rebuild/stop) - same idiom as player.js's IDLE_POLL_MS.
const WS_RECONNECT_MS = 2000;

function connectTrackEvents() {
  if (!tracksColumn) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/ws/tracks`);

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.event === "existing") {
      message.track_ids.forEach(addTrackEntry);
    } else if (message.event === "new_track") {
      addTrackEntry(message.track_id);
    }
  });

  // A close means either the socket dropped or the server-side manager is tearing down (source
  // stopped/switched, so every existing track id is now stale) - clear the column either way and
  // reconnect; a fresh "existing" backfill repopulates it once a pipeline is running again.
  ws.addEventListener("close", () => {
    clearTracks();
    setTimeout(connectTrackEvents, WS_RECONNECT_MS);
  });
  ws.addEventListener("error", () => ws.close());
}

if (mseSupported()) connectTrackEvents();

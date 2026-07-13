// Polls /metrics and renders the live pipeline stats overlay. Values are
// server-side moving averages over a trailing time window, so the panel only
// needs to redraw the latest snapshot every tick.

const POLL_INTERVAL_MS = 500;

// [key, label, unit]. Order defines the rows in the panel.
const ROWS = [
  ["detections_per_frame", "Detections/frame", ""],
  ["active_tracks", "Active tracks", ""],
  ["detection_ms", "Detect (SCRFD)", "ms"],
  ["embedding_ms", "Embed (ArcFace)", "ms"],
  ["batch_ms", "Detection pass", "ms"],
  ["detection_hz", "Detection rate", "Hz"],
  ["processed_fps", "Processed", "fps"],
];

const body = document.getElementById("stats-body");

function render(data) {
  const rows = ROWS.map(([key, label, unit]) => {
    const value = data[key];
    const shown = value === undefined || value === null ? "–" : value;
    return `<tr>
      <td class="stats-label">${label}</td>
      <td class="stats-value">${shown}</td>
      <td class="stats-unit">${unit}</td>
    </tr>`;
  });
  body.innerHTML = rows.join("");
}

async function poll() {
  try {
    const res = await fetch("/metrics", { cache: "no-store" });
    if (res.ok) render(await res.json());
  } catch (_) {
    // Transient during (re)connect; keep the last rendered values.
  }
}

poll();
setInterval(poll, POLL_INTERVAL_MS);

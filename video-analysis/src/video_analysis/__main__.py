import subprocess
import numpy as np
import cv2
from deepface import DeepFace
import supervision as sv

# ─── CONFIG ───────────────────────────────────────────────────────────────────
VIDEO_PATH = "samples/output.mp4"
DETECT_EVERY = 2
CROP_SIZE = (224, 224)          # (width, height)
LOST_TRACK_BUFFER = 60          # frames before a track is considered lost
CROP_MARGIN = 0.3               # relative margin around face box
DETECTOR_BACKEND = "retinaface" # opencv, retinaface, mtcnn, yolov8, centerface, ...
TRACK_COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255),
]
# ──────────────────────────────────────────────────────────────────────────────


def open_reader(path: str) -> tuple[subprocess.Popen, int, int, float]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "csv=p=0", path],
        capture_output=True, text=True
    )
    w, h, fps_frac = probe.stdout.strip().split(",")
    num, den = fps_frac.split("/")
    fps = int(num) / int(den)
    proc = subprocess.Popen(
        ["ffmpeg", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    return proc, int(w), int(h), fps


def open_writer(tid: int, fps: float, size: tuple[int, int] = CROP_SIZE) -> subprocess.Popen:
    w, h = size
    return subprocess.Popen(
        ["ffmpeg", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{w}x{h}", "-r", str(fps),
         "-i", "pipe:0",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         f"face_{tid}.mp4"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
    )


def read_frame(proc: subprocess.Popen, w: int, h: int) -> np.ndarray | None:
    raw = proc.stdout.read(w * h * 3)
    if len(raw) < w * h * 3:
        return None
    return np.frombuffer(raw, np.uint8).reshape((h, w, 3)).copy()


def deepface_to_detections(results: list[dict]) -> sv.Detections:
    xyxy, confs = [], []
    for face in results:
        r = face["facial_area"]
        xyxy.append([r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]])
        confs.append(face.get("confidence", 1.0))
    if not xyxy:
        return sv.Detections.empty()
    return sv.Detections(xyxy=np.array(xyxy, float), confidence=np.array(confs))


def crop_face(
    frame: np.ndarray,
    box: np.ndarray,
    size: tuple[int, int] = CROP_SIZE,
    margin: float = CROP_MARGIN,
) -> np.ndarray:
    x1, y1, x2, y2 = map(int, box)
    fh, fw = frame.shape[:2]
    bw, bh = x2 - x1, y2 - y1
    mx, my = int(bw * margin), int(bh * margin)
    x1, y1 = max(0, x1 - mx), max(0, y1 - my)
    x2, y2 = min(fw, x2 + mx), min(fh, y2 + my)
    crop = frame[y1:y2, x1:x2]
    return cv2.resize(crop, size) if crop.size else np.zeros((size[1], size[0], 3), np.uint8)


def interpolate_box(
    tid: int,
    frame_idx: int,
    prev_known: dict[int, tuple[int, np.ndarray]],
    last_known: dict[int, tuple[int, np.ndarray]],
) -> np.ndarray | None:
    if tid not in last_known:
        return None
    if tid not in prev_known:
        return last_known[tid][1]

    f0, box0 = prev_known[tid]
    f1, box1 = last_known[tid]

    if f0 == f1:
        return box1

    t = np.clip((frame_idx - f0) / (f1 - f0), 0.0, 1.0)
    return box0 + t * (box1 - box0)


def close_writer(tid: int, writers: dict, last_known: dict, prev_known: dict, pending: dict) -> None:
    if tid in writers:
        writers[tid].stdin.close()
        writers[tid].wait()
        del writers[tid]
        last_known.pop(tid, None)
        prev_known.pop(tid, None)
        pending.pop(tid, None)
        print(f"  closed writer for face {tid}")


def open_display(w: int, h: int, fps: float) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffplay", "-f", "rawvideo", "-pixel_format", "rgb24",
         "-video_size", f"{w}x{h}", "-framerate", str(fps),
         "-autoexit", "-"],
        stdin=subprocess.PIPE, stderr=None
    )


def main() -> None:
    reader, W, H, FPS = open_reader(VIDEO_PATH)
    display = open_display(W, H, FPS)
    tracker = sv.ByteTrack(
        lost_track_buffer=LOST_TRACK_BUFFER,
        track_activation_threshold=0.2,
        minimum_matching_threshold=0.7,
    )

    writers:    dict[int, subprocess.Popen]       = {}
    last_known: dict[int, tuple[int, np.ndarray]] = {}
    prev_known: dict[int, tuple[int, np.ndarray]] = {}
    pending:    dict[int, list[bytes]]             = {}  # buffered frames since last detection
    detections = sv.Detections.empty()

    frame_idx = 0
    while True:
        frame = read_frame(reader, W, H)
        print("Frame read")
        if frame is None:
            break

        # ── Detect + track every N frames ─────────────────────────────────
        if frame_idx % DETECT_EVERY == 0:
            try:
                results = DeepFace.extract_faces(
                    frame,
                    detector_backend=DETECTOR_BACKEND,
                    enforce_detection=False,
                    align=False,
                )
                detections = deepface_to_detections(results)
            except Exception as e:
                print(f"  detection error: {e}")
                detections = sv.Detections.empty()

            if len(detections) > 0:
                detections = tracker.update_with_detections(detections)
                for tid, box in zip(detections.tracker_id, detections.xyxy):
                    if tid in last_known:
                        prev_known[tid] = last_known[tid]
                    last_known[tid] = (frame_idx, box.copy())
                    if tid not in writers:
                        print(f"  opened writer for face {tid}")
                        writers[tid] = open_writer(tid, FPS)
                        pending[tid] = []
                    # Face confirmed — flush pending frames
                    for pframe in pending.get(tid, []):
                        writers[tid].stdin.write(pframe)
                    pending[tid] = []

            # ── Close writers for tracks absent too long ───────────────────
            active_ids = (
                set(detections.tracker_id.tolist()) if len(detections) > 0 else set()
            )
            for tid in list(writers.keys()):
                if tid not in active_ids:
                    frames_since_seen = frame_idx - last_known.get(tid, (0,))[0]
                    if frames_since_seen > LOST_TRACK_BUFFER:
                        close_writer(tid, writers, last_known, prev_known, pending)

        # ── Draw circles around tracked faces and display ────────────────
        display_frame = frame.copy()
        for tid in writers:
            box = interpolate_box(tid, frame_idx, prev_known, last_known)
            if box is not None:
                x1, y1, x2, y2 = map(int, box)
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                ax, ay = (x2 - x1) // 2, (y2 - y1) // 2
                color = TRACK_COLORS[tid % len(TRACK_COLORS)]
                cv2.ellipse(display_frame, (cx, cy), (ax, ay), 0, 0, 360, color, 2)
        try:
            display.stdin.write(display_frame[:, :, ::-1].tobytes())
        except BrokenPipeError:
            break

        # ── Buffer every frame using interpolated box ─────────────────────
        for tid in list(writers.keys()):
            box = interpolate_box(tid, frame_idx, prev_known, last_known)
            if box is not None:
                pending.setdefault(tid, []).append(crop_face(frame, box).tobytes())

        frame_idx += 1
        if frame_idx % 100 == 0:
            print(f"frame {frame_idx} | active faces: {len(writers)}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    display.stdin.close()
    display.wait()
    reader.stdout.close()
    reader.wait()
    for tid in list(writers.keys()):
        close_writer(tid, writers, last_known, prev_known, pending)

    print(f"\nDone — processed {frame_idx} frames.")


if __name__ == "__main__":
    main()

import asyncio
import base64
import json

import cv2
import numpy as np
import websockets
from deepface import DeepFace

DETECTOR_BACKEND = "retinaface"
HOST = "0.0.0.0"
PORT = 8765


def warmup():
    """Download model weights by running a dummy detection."""
    print("Downloading model weights...")
    dummy = np.zeros((100, 100, 3), dtype=np.uint8)
    try:
        DeepFace.extract_faces(dummy, detector_backend=DETECTOR_BACKEND, enforce_detection=False, align=False)
    except Exception:
        pass
    print("Model ready.")


async def handle(websocket):
    async for message in websocket:
        frame_bytes = base64.b64decode(message)
        frame = cv2.imdecode(np.frombuffer(frame_bytes, np.uint8), cv2.IMREAD_COLOR)

        try:
            results = DeepFace.extract_faces(
                frame,
                detector_backend=DETECTOR_BACKEND,
                enforce_detection=False,
                align=False,
            )
            faces = []
            for face in results:
                r = face["facial_area"]
                faces.append({
                    "facial_area": {"x": int(r["x"]), "y": int(r["y"]), "w": int(r["w"]), "h": int(r["h"])},
                    "confidence": float(face.get("confidence", 1.0)),
                })
        except Exception:
            faces = []

        await websocket.send(json.dumps({"faces": faces}))


async def main():
    warmup()
    print(f"Listening on {HOST}:{PORT}")
    async with websockets.serve(handle, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

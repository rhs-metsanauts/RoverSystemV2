from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import Generator

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from Config import get_config


class ZedStereoStream:
    def __init__(self) -> None:
        cfg = get_config().get("zed_server", {})
        width, height = self._resolution_to_dimensions(cfg.get("resolution", "HD720"))

        self._lock = threading.Lock()
        self._running = True
        self._jpeg_quality = int(cfg.get("jpeg_quality", 80))
        self._cap = cv2.VideoCapture(0)

        if not self._cap.isOpened():
            raise RuntimeError("failed to open ZED camera on /dev/video0")

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_FPS, int(cfg.get("fps", 30)))

    @staticmethod
    def _resolution_to_dimensions(resolution: str) -> tuple[int, int]:
        mapping = {
            "HD2K": (4416, 1242),
            "HD1080": (3840, 1080),
            "HD720": (2560, 720),
            "VGA": (1344, 376),
        }
        return mapping.get(str(resolution).upper(), (2560, 720))

    def close(self) -> None:
        with self._lock:
            self._running = False
            if self._cap.isOpened():
                self._cap.release()

    def _read_stereo(self) -> tuple[np.ndarray, np.ndarray] | None:
        with self._lock:
            if not self._running:
                return None
            ok, frame = self._cap.read()

        if not ok or frame is None or frame.size == 0:
            return None

        # ZED USB capture is side-by-side: left eye on one half, right eye on the other.
        split = np.split(frame, 2, axis=1)
        if len(split) != 2:
            return None
        return split[0], split[1]

    def generate_mjpeg(self, eye: str) -> Generator[bytes, None, None]:
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]

        while self._running:
            stereo = self._read_stereo()
            if stereo is None:
                continue

            left, right = stereo
            frame = left if eye == "left" else right

            ok, jpg = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpg.tobytes()
                + b"\r\n"
            )


zed_stream: ZedStereoStream | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global zed_stream
    zed_stream = ZedStereoStream()
    try:
        yield
    finally:
        if zed_stream is not None:
            zed_stream.close()


app = FastAPI(
    title="zed-2i-stream-server",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _stream_response(eye: str) -> StreamingResponse:
    if zed_stream is None:
        raise HTTPException(status_code=503, detail="zed camera not initialized")
    return StreamingResponse(
        zed_stream.generate_mjpeg(eye),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/left.mjpg")
def left_stream() -> StreamingResponse:
    return _stream_response("left")


@app.get("/right.mjpg")
def right_stream() -> StreamingResponse:
    return _stream_response("right")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ZedFastApiServer:app", host="0.0.0.0", port=8001, reload=False)

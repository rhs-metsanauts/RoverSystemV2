import json
import io
import threading
import time
from typing import Any, Dict, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image

from Config import get_config

try:
    import pyzed.sl as sl
except ImportError:  # pragma: no cover - depends on Jetson setup
    sl = None


class ZedSlamStreamer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None

        self.latest_frame: Optional[bytes] = None
        self.latest_pose: Dict[str, Any] = {
            "tracking_state": "NOT_INITIALIZED",
            "timestamp_ns": 0,
            "position_m": [0.0, 0.0, 0.0],
            "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
            "pose_confidence": 0,
        }

        self.zed = None
        self.image_mat = None
        self.pose = None
        self.runtime_params = None
        self.jpeg_quality = int(self.config.get("jpeg_quality", 80))

    def _resolve_resolution(self):
        requested = str(self.config.get("resolution", "HD720")).upper()
        candidates = {
            "HD2K": ["HD2K"],
            "HD1200": ["HD1200"],
            "HD1080": ["HD1080"],
            "HD720": ["HD720"],
            "SVGA": ["SVGA"],
            "VGA": ["VGA"],
            "AUTO": ["AUTO"],
        }
        for candidate in candidates.get(requested, ["HD720"]):
            if hasattr(sl.RESOLUTION, candidate):
                return getattr(sl.RESOLUTION, candidate)
        return sl.RESOLUTION.HD720

    @staticmethod
    def _enum_to_text(value):
        if hasattr(value, "name"):
            return value.name
        return str(value)

    @staticmethod
    def _extract_translation(pose):
        try:
            translation = sl.Translation()
            pose.get_translation(translation)
            values = translation.get()
            return [float(values[0]), float(values[1]), float(values[2])]
        except Exception:
            pass

        try:
            translation = pose.get_translation()
            if hasattr(translation, "get"):
                values = translation.get()
                return [float(values[0]), float(values[1]), float(values[2])]
            return [float(translation.tx), float(translation.ty), float(translation.tz)]
        except Exception:
            return [0.0, 0.0, 0.0]

    @staticmethod
    def _extract_orientation(pose):
        try:
            orientation = sl.Orientation()
            pose.get_orientation(orientation)
            values = orientation.get()
            return [float(values[0]), float(values[1]), float(values[2]), float(values[3])]
        except Exception:
            pass

        try:
            orientation = pose.get_orientation()
            if hasattr(orientation, "get"):
                values = orientation.get()
                return [float(values[0]), float(values[1]), float(values[2]), float(values[3])]
            return [
                float(orientation.ox),
                float(orientation.oy),
                float(orientation.oz),
                float(orientation.ow),
            ]
        except Exception:
            return [0.0, 0.0, 0.0, 1.0]

    @staticmethod
    def _extract_timestamp_ns(pose):
        try:
            return int(pose.timestamp.get_nanoseconds())
        except Exception:
            pass

        try:
            return int(pose.timestamp.get_microseconds()) * 1000
        except Exception:
            pass

        return int(time.time() * 1_000_000_000)

    @staticmethod
    def _extract_pose_confidence(pose):
        for attr in ["pose_confidence", "confidence"]:
            if hasattr(pose, attr):
                try:
                    return int(getattr(pose, attr))
                except Exception:
                    continue
        return 0

    def start(self):
        if sl is None:
            raise RuntimeError("pyzed is not installed. Install ZED Python API first.")

        self.zed = sl.Camera()

        init = sl.InitParameters()
        init.camera_resolution = self._resolve_resolution()
        init.camera_fps = int(self.config.get("fps", 30))
        init.coordinate_system = sl.COORDINATE_SYSTEM.RIGHT_HANDED_Y_UP
        init.coordinate_units = sl.UNIT.METER

        # Depth is required for robust positional tracking.
        if hasattr(sl.DEPTH_MODE, "PERFORMANCE"):
            init.depth_mode = sl.DEPTH_MODE.PERFORMANCE

        open_status = self.zed.open(init)
        if open_status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"Failed to open ZED camera: {self._enum_to_text(open_status)}")

        tracking_params = sl.PositionalTrackingParameters()
        tracking_params.enable_area_memory = bool(self.config.get("area_memory", False))

        if hasattr(sl.POSITIONAL_TRACKING_MODE, "GEN_3"):
            tracking_params.mode = sl.POSITIONAL_TRACKING_MODE.GEN_3
        elif hasattr(sl.POSITIONAL_TRACKING_MODE, "GEN3"):
            tracking_params.mode = sl.POSITIONAL_TRACKING_MODE.GEN3

        tracking_status = self.zed.enable_positional_tracking(tracking_params)
        if tracking_status != sl.ERROR_CODE.SUCCESS:
            self.zed.close()
            raise RuntimeError(
                f"Failed to enable positional tracking: {self._enum_to_text(tracking_status)}"
            )

        self.runtime_params = sl.RuntimeParameters()
        self.image_mat = sl.Mat()
        self.pose = sl.Pose()

        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        if self.zed is not None:
            try:
                self.zed.disable_positional_tracking()
            except Exception:
                pass
            self.zed.close()

    def _capture_loop(self):
        while self.running:
            if self.zed.grab(self.runtime_params) != sl.ERROR_CODE.SUCCESS:
                time.sleep(0.001)
                continue

            self.zed.retrieve_image(self.image_mat, sl.VIEW.LEFT)
            raw_frame = self.image_mat.get_data()

            if raw_frame is not None:
                if hasattr(raw_frame, "get"):
                    try:
                        raw_frame = raw_frame.get()
                    except Exception:
                        pass

                try:
                    frame = np.array(raw_frame, copy=True)
                except Exception:
                    frame = None

                if isinstance(frame, np.ndarray) and frame.size > 0:
                    frame = np.ascontiguousarray(frame)

                    if frame.dtype != np.uint8:
                        try:
                            frame = frame.astype(np.uint8, copy=False)
                        except Exception:
                            frame = None

                if isinstance(frame, np.ndarray) and frame.size > 0:
                    if frame.ndim > 3:
                        frame = np.squeeze(frame)

                    if frame.ndim == 3 and frame.shape[2] == 4:
                        frame = frame[:, :, :3]
                    elif frame.ndim == 2:
                        frame = np.repeat(frame[:, :, None], 3, axis=2)

                    if frame.ndim == 3 and frame.shape[2] == 3:
                        frame = np.ascontiguousarray(frame, dtype=np.uint8)
                        rgb = frame[:, :, ::-1]
                        jpeg_buffer = io.BytesIO()
                        Image.fromarray(rgb, mode="RGB").save(
                            jpeg_buffer,
                            format="JPEG",
                            quality=self.jpeg_quality,
                            optimize=True,
                        )
                        with self.lock:
                            self.latest_frame = jpeg_buffer.getvalue()

            tracking_state = self.zed.get_position(self.pose, sl.REFERENCE_FRAME.WORLD)
            pose_payload = {
                "tracking_state": self._enum_to_text(tracking_state),
                "timestamp_ns": self._extract_timestamp_ns(self.pose),
                "position_m": self._extract_translation(self.pose),
                "orientation_xyzw": self._extract_orientation(self.pose),
                "pose_confidence": self._extract_pose_confidence(self.pose),
            }

            with self.lock:
                self.latest_pose = pose_payload

    def get_pose_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.latest_pose)

    def mjpeg_generator(self):
        while True:
            with self.lock:
                frame = self.latest_frame

            if frame is None:
                time.sleep(0.01)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )

    async def pose_sse_generator(self):
        pose_hz = max(1, int(self.config.get("pose_stream_hz", 10)))
        sleep_seconds = 1.0 / pose_hz
        last_sent_timestamp = None

        while True:
            pose = self.get_pose_snapshot()
            timestamp = pose.get("timestamp_ns")

            if timestamp != last_sent_timestamp:
                payload = json.dumps(pose, separators=(",", ":"))
                yield f"event: pose\\ndata: {payload}\\n\\n"
                last_sent_timestamp = timestamp

            await self._async_sleep(sleep_seconds)

    @staticmethod
    async def _async_sleep(seconds: float):
        import asyncio

        await asyncio.sleep(seconds)


app = FastAPI(title="ZED 2i Video + SLAM Server")

_app_config = get_config()
_zed_server_config = _app_config.get("zed_server", {})
streamer = ZedSlamStreamer(_zed_server_config)


@app.on_event("startup")
def startup_event():
    streamer.start()


@app.on_event("shutdown")
def shutdown_event():
    streamer.stop()


@app.get("/")
def index():
    return {
        "service": "zed-fastapi",
        "video_stream": "/video.mjpg",
        "latest_pose": "/pose",
        "pose_stream": "/pose/stream",
    }


@app.get("/video.mjpg")
def video_stream():
    return StreamingResponse(
        streamer.mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/pose")
def pose_snapshot():
    return JSONResponse(streamer.get_pose_snapshot())


@app.get("/pose/stream")
async def pose_stream():
    return StreamingResponse(
        streamer.pose_sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/health")
def health():
    pose = streamer.get_pose_snapshot()
    if pose.get("tracking_state") == "NOT_INITIALIZED":
        raise HTTPException(status_code=503, detail="ZED tracking not initialized")
    return {"status": "ok", "tracking_state": pose.get("tracking_state")}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ZedFastApiServer:app", host="0.0.0.0", port=8001, reload=False)

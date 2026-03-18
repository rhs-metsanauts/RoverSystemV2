import pyzed.sl as sl
import numpy as np
import cv2
import json
import math
import threading


class ZedCamera:
    def __init__(self):
        self.zed = sl.Camera()

        init_params = sl.InitParameters()
        init_params.coordinate_units = sl.UNIT.METER
        init_params.coordinate_system = sl.COORDINATE_SYSTEM.RIGHT_HANDED_Y_UP
        init_params.camera_resolution = sl.RESOLUTION.HD720
        init_params.camera_fps = 30

        if self.zed.open(init_params) != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError("Failed to open ZED camera")

        # Enable positional tracking / localization
        tracking_params = sl.PositionalTrackingParameters()
        tracking_params.enable_area_memory = True  # enables loop closure & re-localization
        self.zed.enable_positional_tracking(tracking_params)

        # Enable spatial mapping
        mapping_params = sl.SpatialMappingParameters()
        mapping_params.map_type = sl.SPATIAL_MAP_TYPE.FUSED_POINT_CLOUD
        mapping_params.resolution_meter = 0.05  # 5cm resolution
        mapping_params.range_meter = 5.0
        self.zed.enable_spatial_mapping(mapping_params)

        self.runtime_params = sl.RuntimeParameters()
        self._pose = sl.Pose()
        self._point_cloud_map = sl.FusedPointCloud()
        self._image = sl.Mat()

        # Cache latest pose so reads from HTTP threads are non-blocking
        self._latest_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._latest_rotation = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        self._tracking_state = "UNAVAILABLE"
        self._lock = threading.Lock()

        # Background thread keeps pose cache fresh
        self._running = True
        self._grab_thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._grab_thread.start()

    # -------------------------------------------------------------------------
    # Internal grab loop (runs in background thread)
    # -------------------------------------------------------------------------

    def _grab_loop(self):
        while self._running:
            if self.zed.grab(self.runtime_params) == sl.ERROR_CODE.SUCCESS:
                state = self.zed.get_position(self._pose, sl.REFERENCE_FRAME.WORLD)
                translation = self._pose.get_translation(sl.Translation()).get()
                rotation = self._pose.get_euler_angles()  # roll, pitch, yaw in degrees

                with self._lock:
                    self._latest_position = {
                        "x": float(translation[0]),
                        "y": float(translation[1]),
                        "z": float(translation[2]),
                    }
                    self._latest_rotation = {
                        "roll":  float(rotation[0]),
                        "pitch": float(rotation[1]),
                        "yaw":   float(rotation[2]),
                    }
                    self._tracking_state = str(state)

    # -------------------------------------------------------------------------
    # Position & map accessors
    # -------------------------------------------------------------------------

    def get_position(self) -> dict:
        """Return the latest 6-DoF pose as a plain dict."""
        with self._lock:
            return {
                "position": dict(self._latest_position),
                "rotation": dict(self._latest_rotation),
                "tracking_state": self._tracking_state,
            }

    def get_map_2d(self) -> dict:
        """
        Extract the current fused point cloud and project it to a top-down
        2-D map (X-Z plane).  Returns a dict with a list of {x, z} points
        plus the current robot position for overlay.
        """
        self.zed.extract_whole_spatial_map(self._point_cloud_map)
        vertices = np.array(self._point_cloud_map.vertices.get())

        points = []
        if len(vertices) > 0:
            # Optional height filter — keep points near floor level
            mask = (vertices[:, 1] > 0.05) & (vertices[:, 1] < 2.5)
            filtered = vertices[mask]
            # Downsample to keep the JSON payload manageable
            step = max(1, len(filtered) // 5000)
            sampled = filtered[::step]
            points = [{"x": float(p[0]), "z": float(p[2])} for p in sampled]

        with self._lock:
            robot_pos = dict(self._latest_position)

        return {
            "points": points,
            "robot": {
                "x": robot_pos["x"],
                "z": robot_pos["z"],
                "yaw": self._latest_rotation["yaw"],
            },
        }

    def save_area_map(self, path: str = "zed_area.area"):
        """Persist the area memory so the camera can re-localize on next run."""
        self.zed.save_area_map(path)

    def load_area_map(self, path: str = "zed_area.area"):
        """Load a previously saved area map for instant re-localization."""
        self.zed.load_area_map(path)

    # -------------------------------------------------------------------------
    # HTTP streaming helpers  (mirrors UsbCamera.generate_frames)
    # -------------------------------------------------------------------------

    def generate_frames(self):
        """
        Continuously yield JPEG frames from the ZED left camera for
        multipart HTTP streaming  (drop-in replacement for UsbCamera).
        """
        while True:
            if self.zed.grab(self.runtime_params) == sl.ERROR_CODE.SUCCESS:
                self.zed.retrieve_image(self._image, sl.VIEW.LEFT)
                frame = self._image.get_data()          # BGRA numpy array
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                _, buffer = cv2.imencode('.jpg', frame_bgr)
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + buffer.tobytes()
                    + b'\r\n'
                )

    def generate_position_stream(self):
        """
        Yield the latest pose as a newline-delimited JSON stream
        (Server-Sent Events format) for real-time position updates over HTTP.
        """
        import time
        while True:
            data = json.dumps(self.get_position())
            yield f"data: {data}\n\n"
            time.sleep(0.05)  # ~20 Hz

    def generate_map_stream(self):
        """
        Yield 2-D map snapshots as a newline-delimited JSON stream.
        Map extraction is expensive, so updates are throttled to ~1 Hz.
        """
        import time
        while True:
            data = json.dumps(self.get_map_2d())
            yield f"data: {data}\n\n"
            time.sleep(1.0)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def close(self):
        self._running = False
        self._grab_thread.join(timeout=2.0)
        self.zed.disable_spatial_mapping()
        self.zed.disable_positional_tracking()
        self.zed.close()
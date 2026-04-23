# jetson_mapper.py
"""
ZED 2i spatial mapping streamer for Jetson Orin.
Run: python3 jetson_mapper.py
Requires: pyzed (ZED SDK), numpy, websockets==12.0
"""

import asyncio
import json
import numpy as np

try:
    import pyzed.sl as sl
    ZED_AVAILABLE = True
except ImportError:
    ZED_AVAILABLE = False

import websockets
import websockets.exceptions
import cv2

VOXEL_SIZE   = 0.05   # metres — 5 cm grid
WS_PORT      = 9001
REQUEST_EVERY = 30    # request new mesh every N grabs
BROADCAST_INTERVAL = 0.5  # seconds between WebSocket updates
MAX_MESH_VERTICES = 20000
MAX_MESH_TRIANGLES = 40000


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_voxel_key(x, y, z, voxel_size=VOXEL_SIZE):
    return (int(np.floor(x / voxel_size)),
            int(np.floor(y / voxel_size)),
            int(np.floor(z / voxel_size)))


def voxel_downsample(vertices, colors, voxel_size=VOXEL_SIZE):
    if len(vertices) == 0:
        return vertices, colors, set()
    seen = {}
    for i in range(len(vertices)):
        key = get_voxel_key(float(vertices[i, 0]), float(vertices[i, 1]), float(vertices[i, 2]), voxel_size)
        if key not in seen:
            seen[key] = i
    keep = np.array(list(seen.values()), dtype=np.int32)
    return vertices[keep], colors[keep], set(seen.keys())


def fallback_colors(vertices):
    if len(vertices) == 0:
        return np.zeros((0, 3), dtype=np.float32)
    y = vertices[:, 1]
    y_min, y_max = float(y.min()), float(y.max())
    y_range = max(y_max - y_min, 1e-6)
    t = (y - y_min) / y_range
    r = np.clip(t * 2.0 - 1.0, 0.0, 1.0)
    g = np.clip(1.0 - np.abs(t * 2.0 - 1.0), 0.0, 1.0)
    b = np.clip(1.0 - t * 2.0, 0.0, 1.0)
    return np.stack([r, g, b], axis=1).astype(np.float32)


def _packed_to_rgb(packed):
    packed_u32 = np.asarray(packed).view(np.uint32)
    # ZED packed color is typically little-endian BGRA when read as uint32.
    b = ((packed_u32 >> 0) & 0xFF).astype(np.float32)
    g = ((packed_u32 >> 8) & 0xFF).astype(np.float32)
    r = ((packed_u32 >> 16) & 0xFF).astype(np.float32)
    rgb = np.stack([r, g, b], axis=1) / 255.0
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def extract_actual_colors(spatial_map, vertices, valid_mask=None):
    """Extract RGB colors from spatial map, with safe fallback if unavailable."""
    raw_colors = getattr(spatial_map, "colors", None)

    if raw_colors is not None:
        arr = np.asarray(raw_colors)
        if valid_mask is not None and len(valid_mask) == len(arr):
            arr = arr[valid_mask]
        if arr.ndim == 2 and arr.shape[1] >= 3:
            rgb = arr[:, :3].astype(np.float32)
            if rgb.max(initial=0.0) > 1.0:
                rgb /= 255.0
            if len(rgb) == len(vertices):
                return np.clip(rgb, 0.0, 1.0)
        if arr.ndim == 1 and len(arr) == len(vertices):
            return _packed_to_rgb(arr)

    if vertices.ndim == 2 and vertices.shape[1] >= 4:
        return _packed_to_rgb(vertices[:, 3].astype(np.float32))

    return fallback_colors(vertices)


class VoxelTracker:
    def __init__(self, voxel_size=VOXEL_SIZE):
        self.voxel_size = voxel_size
        self._sent = set()

    def get_new_points(self, vertices, colors):
        if len(vertices) == 0:
            return []
        result = []
        for i in range(len(vertices)):
            key = get_voxel_key(float(vertices[i, 0]), float(vertices[i, 1]), float(vertices[i, 2]), self.voxel_size)
            if key not in self._sent:
                self._sent.add(key)
                r = int(np.clip(colors[i, 0], 0.0, 1.0) * 255)
                g = int(np.clip(colors[i, 1], 0.0, 1.0) * 255)
                b = int(np.clip(colors[i, 2], 0.0, 1.0) * 255)
                result.append([round(float(vertices[i, 0]), 4),
                                round(float(vertices[i, 1]), 4),
                                round(float(vertices[i, 2]), 4),
                                r, g, b])
        return result

    def clear(self):
        self._sent.clear()


# ── ZedMapper ─────────────────────────────────────────────────────────────────

class ZedMapper:
    def __init__(self):
        self.tracker = VoxelTracker()
        self.clients = set()
        self.mapping_active = False
        self.seq = 0

    def _init_zed(self):
        if not ZED_AVAILABLE:
            raise RuntimeError("pyzed not installed")

        self.zed = sl.Camera()
        init = sl.InitParameters()
        init.coordinate_units = sl.UNIT.METER
        init.coordinate_system = sl.COORDINATE_SYSTEM.RIGHT_HANDED_Y_UP
        init.depth_mode = sl.DEPTH_MODE.PERFORMANCE

        status = self.zed.open(init)
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"ZED open failed: {status}")

        status = self.zed.enable_positional_tracking(sl.PositionalTrackingParameters())
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"Tracking failed: {status}")

        # runtime helpers for extra features
        self.runtime_params = sl.RuntimeParameters()
        self._pose = sl.Pose()
        self._spatial_map = sl.Mesh()
        self._image = sl.Mat()

        self._enable_mapping()
        print("[ZedMapper] ZED 2i initialised, spatial mapping enabled")

    def _enable_mapping(self):
        mp = sl.SpatialMappingParameters()
        mp.map_type = sl.SPATIAL_MAP_TYPE.MESH
        mp.resolution_meter = VOXEL_SIZE
        mp.range_meter = 8.0
        status = self.zed.enable_spatial_mapping(mp)
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"Spatial mapping failed: {status}")

    def _get_rover_pos(self):
        state = self.zed.get_position(self._pose, sl.REFERENCE_FRAME.WORLD)
        if state == sl.POSITIONAL_TRACKING_STATE.OK:
            t = self._pose.get_translation(sl.Translation())
            v = t.get()
            return [round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)]
        return [0.0, 0.0, 0.0]

    def get_position(self):
        """Return current pose including Euler angles."""
        state = self.zed.get_position(self._pose, sl.REFERENCE_FRAME.WORLD)
        if state == sl.POSITIONAL_TRACKING_STATE.OK:
            t = self._pose.get_translation(sl.Translation()).get()
            rot = self._pose.get_euler_angles()  # roll, pitch, yaw
            return {
                "position": {"x": float(t[0]), "y": float(t[1]), "z": float(t[2])},
                "rotation": {"roll": float(rot[0]), "pitch": float(rot[1]), "yaw": float(rot[2])},
                "tracking_state": str(state),
            }
        return {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}, "tracking_state": str(state)}

    def _get_points(self):
        """Retrieve current mesh vertices, downsample, return new points."""
        self.zed.extract_whole_spatial_map(self._spatial_map)

        raw = self._spatial_map.vertices
        if raw is None or len(raw) == 0:
            return []

        vertices = np.array(raw, dtype=np.float32)

        # Keep only numerically valid XYZ points.
        valid = np.isfinite(vertices[:, 0]) & np.isfinite(vertices[:, 1]) & np.isfinite(vertices[:, 2])
        vertices = vertices[valid]
        if len(vertices) == 0:
            return []

        colors = extract_actual_colors(self._spatial_map, vertices, valid)
        v_down, c_down, _ = voxel_downsample(vertices, colors)
        return self.tracker.get_new_points(v_down, c_down)

    def get_mesh(self, max_vertices=MAX_MESH_VERTICES, max_triangles=MAX_MESH_TRIANGLES) -> dict:
        """Return a compact mesh payload with vertices and triangles for rendering."""
        self.zed.extract_whole_spatial_map(self._spatial_map)

        raw_vertices = self._spatial_map.vertices
        raw_triangles = getattr(self._spatial_map, "triangles", None)

        if raw_vertices is None or len(raw_vertices) == 0:
            return {"vertices": [], "triangles": [], "robot": self.get_position()["position"]}

        vertices = np.array(raw_vertices, dtype=np.float32)
        valid = np.isfinite(vertices[:, 0]) & np.isfinite(vertices[:, 1]) & np.isfinite(vertices[:, 2])
        vertices = vertices[valid]
        if len(vertices) == 0:
            return {"vertices": [], "triangles": [], "robot": self.get_position()["position"]}

        if len(vertices) > max_vertices:
            step = max(1, len(vertices) // max_vertices)
            vertices = vertices[::step]

        triangles = np.zeros((0, 3), dtype=np.int32)
        if raw_triangles is not None and len(raw_triangles) > 0:
            tri = np.array(raw_triangles, dtype=np.int32)
            if tri.ndim == 1 and len(tri) % 3 == 0:
                tri = tri.reshape(-1, 3)
            if tri.ndim == 2 and tri.shape[1] >= 3:
                tri = tri[:, :3]
                tri = tri[(tri[:, 0] < len(vertices)) & (tri[:, 1] < len(vertices)) & (tri[:, 2] < len(vertices))]
                if len(tri) > max_triangles:
                    tri_step = max(1, len(tri) // max_triangles)
                    tri = tri[::tri_step]
                triangles = tri.astype(np.int32)

        compact_vertices = [[round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)] for v in vertices]
        compact_triangles = [[int(t[0]), int(t[1]), int(t[2])] for t in triangles]

        return {
            "vertices": compact_vertices,
            "triangles": compact_triangles,
            "robot": self.get_position()["position"],
        }

    # ------------------------------------------------------------------
    # 2-D map / HTTP streaming helpers (compatible with ZedCamera interface)
    # ------------------------------------------------------------------

    def get_map_2d(self) -> dict:
        """Project fused point cloud to X-Z plane and return a compact map snapshot."""
        self.zed.extract_whole_spatial_map(self._spatial_map)
        raw_vertices = self._spatial_map.vertices

        points = []
        if raw_vertices is not None and len(raw_vertices) > 0:
            vertices = np.array(raw_vertices, dtype=np.float32)
            valid = np.isfinite(vertices[:, 0]) & np.isfinite(vertices[:, 1]) & np.isfinite(vertices[:, 2])
            vertices = vertices[valid]
            if len(vertices) == 0:
                return {"points": points, "robot": {"x": 0.0, "z": 0.0, "yaw": 0.0}}

            colors = extract_actual_colors(self._spatial_map, vertices, valid)

            # Keep map points in a practical scene band and mark object points above floor.
            mask = (vertices[:, 1] > -0.1) & (vertices[:, 1] < 2.5)
            filtered = vertices[mask]
            filtered_colors = colors[mask]
            step = max(1, len(filtered) // 5000)
            sampled = filtered[::step]
            sampled_colors = filtered_colors[::step]

            for i in range(len(sampled)):
                p = sampled[i]
                c = sampled_colors[i]
                points.append({
                    "x": float(p[0]),
                    "z": float(p[2]),
                    "y": float(p[1]),
                    "r": int(np.clip(c[0], 0.0, 1.0) * 255),
                    "g": int(np.clip(c[1], 0.0, 1.0) * 255),
                    "b": int(np.clip(c[2], 0.0, 1.0) * 255),
                    "kind": "object" if float(p[1]) > 0.15 else "floor",
                })

        rover_pos = {"x": 0.0, "z": 0.0, "yaw": 0.0}
        pos = self.get_position()
        rover_pos["x"] = pos["position"]["x"]
        rover_pos["z"] = pos["position"]["z"]
        rover_pos["yaw"] = pos["rotation"]["yaw"]

        return {"points": points, "robot": rover_pos}

    def generate_frames(self):
        """Yield JPEG frames from left camera for HTTP multipart streaming."""
        while True:
            if self.zed.grab(self.runtime_params) == sl.ERROR_CODE.SUCCESS:
                self.zed.retrieve_image(self._image, sl.VIEW.LEFT)
                frame = self._image.get_data()
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                _, buffer = cv2.imencode('.jpg', frame_bgr)
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n'
                    + buffer.tobytes()
                    + b'\r\n'
                )

    def save_area_map(self, path: str = "zed_area.area"):
        self.zed.save_area_map(path)

    def load_area_map(self, path: str = "zed_area.area"):
        self.zed.load_area_map(path)

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _broadcast(self, message):
        if not self.clients:
            return
        results = await asyncio.gather(
            *[ws.send(message) for ws in list(self.clients)],
            return_exceptions=True,
        )
        for ws, result in zip(list(self.clients), results):
            if isinstance(result, Exception):
                self.clients.discard(ws)

    async def _handle_client(self, websocket, path=""):
        self.clients.add(websocket)
        print(f"[ZedMapper] Client connected: {websocket.remote_address}")
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    action = msg.get("action")
                    if action == "start":
                        self.mapping_active = True
                        print("[ZedMapper] Mapping started")
                    elif action == "stop":
                        self.mapping_active = False
                        print("[ZedMapper] Mapping stopped")
                    elif action == "clear":
                        self.mapping_active = False
                        self.tracker.clear()
                        self.seq = 0
                        self.zed.disable_spatial_mapping()
                        self._enable_mapping()
                        await websocket.send(json.dumps({"type": "cleared"}))
                        print("[ZedMapper] Map cleared")
                except (json.JSONDecodeError, KeyError):
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            print("[ZedMapper] Client disconnected")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _mapping_loop(self):
        last_broadcast = 0.0

        while True:
            if self.mapping_active:
                if self.zed.grab() == sl.ERROR_CODE.SUCCESS:
                    now = asyncio.get_event_loop().time()
                    if now - last_broadcast >= BROADCAST_INTERVAL:
                        last_broadcast = now
                        new_pts = self._get_points()
                        if new_pts:
                            rover_pos = self._get_rover_pos()
                            payload = json.dumps({
                                "type": "chunk",
                                "seq": self.seq,
                                "rover_pos": rover_pos,
                                "points": new_pts,
                            })
                            self.seq += 1
                            await self._broadcast(payload)

                await asyncio.sleep(0)
            else:
                grab_count = 0
                await asyncio.sleep(0.1)

    async def run(self):
        self._init_zed()
        print(f"[ZedMapper] WebSocket server on ws://0.0.0.0:{WS_PORT}")
        async with websockets.serve(self._handle_client, "0.0.0.0", WS_PORT):
            await self._mapping_loop()

    def cleanup(self):
        if ZED_AVAILABLE and hasattr(self, "zed"):
            self.zed.disable_spatial_mapping()
            self.zed.disable_positional_tracking()
            self.zed.close()


if __name__ == "__main__":
    mapper = ZedMapper()
    try:
        asyncio.run(mapper.run())
    except KeyboardInterrupt:
        print("\n[ZedMapper] Shutting down…")
    finally:
        mapper.cleanup()

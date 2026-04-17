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

VOXEL_SIZE   = 0.05   # metres — 5 cm grid
WS_PORT      = 9001
REQUEST_EVERY = 30    # request new mesh every N grabs
BROADCAST_INTERVAL = 0.5  # seconds between WebSocket updates


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


def height_colors(vertices):
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

        self._enable_mapping()
        print("[ZedMapper] ZED 2i initialised, spatial mapping enabled")

    def _enable_mapping(self):
        mp = sl.SpatialMappingParameters()
        mp.map_type = sl.SPATIAL_MAP_TYPE.FUSED_POINT_CLOUD  # more reliable than Mesh in SDK 4.x
        mp.resolution_meter = VOXEL_SIZE
        mp.range_meter = 8.0
        status = self.zed.enable_spatial_mapping(mp)
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"Spatial mapping failed: {status}")

    def _get_rover_pos(self):
        pose = sl.Pose()
        state = self.zed.get_position(pose, sl.REFERENCE_FRAME.WORLD)
        if state == sl.POSITIONAL_TRACKING_STATE.OK:
            t = pose.get_translation(sl.Translation())
            v = t.get()
            return [round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)]
        return [0.0, 0.0, 0.0]

    def _get_points(self):
        """Retrieve current fused point cloud, downsample, return new points."""
        fpc = sl.FusedPointCloud()
        self.zed.extract_whole_spatial_map(fpc)

        raw = fpc.vertices
        if raw is None or len(raw) == 0:
            return []

        vertices = np.array(raw, dtype=np.float32)
        colors = height_colors(vertices)
        v_down, c_down, _ = voxel_downsample(vertices, colors)
        return self.tracker.get_new_points(v_down, c_down)

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

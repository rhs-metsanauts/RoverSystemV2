from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
import socket

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
try:
    _LABEL_FONT = ImageFont.truetype(_FONT_PATH, 15)
except Exception:
    _LABEL_FONT = ImageFont.load_default()

# RGB colours per object class — mirrors the Three.js OBJ_COLORS map
_OBJ_COLORS_RGB: dict[str, tuple[int, int, int]] = {
    "PERSON":          (0,   229, 255),
    "VEHICLE":         (255, 109,   0),
    "ANIMAL":          (118, 255,   3),
    "BAG":             (255, 234,   0),
    "ELECTRONICS":     (224,  64, 251),
    "FRUIT_VEGETABLE": (255,  64, 129),
    "SPORT":           (105, 240, 174),
}
import websockets
from websockets.server import WebSocketServerProtocol

try:
    import pyzed.sl as sl
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pyzed.sl is required for jetson_mapper.py") from exc


@dataclass
class MapperConfig:
    ws_host: str = os.getenv("MAPPER_WS_HOST", "0.0.0.0")
    ws_port: int = int(os.getenv("MAPPER_WS_PORT", "9001"))
    mjpeg_host: str = os.getenv("MAPPER_MJPEG_HOST", "0.0.0.0")
    mjpeg_port: int = int(os.getenv("MAPPER_MJPEG_PORT", "8001"))
    rover_id: str = socket.gethostname()
    voxel_size: float = float(os.getenv("MAPPER_VOXEL_SIZE", "0.15"))
    range_m: float = float(os.getenv("MAPPER_RANGE_M", "5.0"))
    fps: int = int(os.getenv("MAPPER_FPS", "15"))
    # Extract and broadcast mesh every N grabs (default ~4 s at 15 fps)
    mesh_interval: int = int(os.getenv("MAPPER_MESH_INTERVAL", "20"))
    max_faces: int = int(os.getenv("MAPPER_MAX_FACES", "4000"))


class JetsonMapper:
    def __init__(self, config: MapperConfig) -> None:
        self.config = config
        self.clients: set[WebSocketServerProtocol] = set()
        self.mapping_active = False
        self.seq = 0
        self._grab_count = 0
        self.session_id = f"session-{int(asyncio.get_event_loop().time() * 1000)}"
        self._mjpeg_queues: dict[int, asyncio.Queue[bytes]] = {}
        self._mjpeg_paths: dict[int, str] = {}
        self._mat = sl.Mat()
        self._sensors = sl.SensorsData()

        self._init_camera()

    def _init_camera(self) -> None:
        self.zed = sl.Camera()

        init = sl.InitParameters()
        init.coordinate_units = sl.UNIT.METER
        init.coordinate_system = sl.COORDINATE_SYSTEM.RIGHT_HANDED_Y_UP
        init.depth_mode = sl.DEPTH_MODE.PERFORMANCE

        status = self.zed.open(init)
        if status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"failed to open ZED camera: {status}")

        # FIX: configure RuntimeParameters with depth confidence thresholds
        # rather than relying on bare defaults, as recommended by SDK guidance.
        self.runtime = sl.RuntimeParameters()
        self.runtime.confidence_threshold = 50
        self.runtime.texture_confidence_threshold = 100

        self.pose = sl.Pose()

        tracking_params = sl.PositionalTrackingParameters()
        tracking_params.enable_area_memory = True
        tracking = self.zed.enable_positional_tracking(tracking_params)
        if tracking != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"failed to enable positional tracking: {tracking}")

        self._enable_spatial_mapping()

        self._od_enabled = False
        try:
            od_params = sl.ObjectDetectionParameters()
            od_params.detection_model = sl.OBJECT_DETECTION_MODEL.MULTI_CLASS_BOX_MEDIUM
            od_params.enable_tracking = True
            od_params.max_range = self.config.range_m
            od_status = self.zed.enable_object_detection(od_params)
            if od_status == sl.ERROR_CODE.SUCCESS:
                self._objects = sl.Objects()
                self._obj_rt = sl.ObjectDetectionRuntimeParameters()
                self._obj_rt.detection_confidence_threshold = 40
                self._od_enabled = True
                print("object detection enabled")
            else:
                print(f"[warn] object detection unavailable: {od_status}")
        except Exception as e:
            print(f"[warn] object detection init failed: {e}")

    def _enable_spatial_mapping(self) -> None:
        """Encapsulated so it can be called both on init and after clear_mapping."""
        mapping = sl.SpatialMappingParameters()
        mapping.map_type = sl.SPATIAL_MAP_TYPE.MESH
        mapping.resolution_meter = self.config.voxel_size
        mapping.range_meter = self.config.range_m
        mapping.max_memory_usage = 1024  # MB — keeps mapping within Jetson's budget
        mapping.save_texture = True

        map_status = self.zed.enable_spatial_mapping(mapping)
        if map_status != sl.ERROR_CODE.SUCCESS:
            raise RuntimeError(f"failed to enable spatial mapping: {map_status}")

    # ------------------------------------------------------------------ MJPEG

    def _grab_frame(
        self, capture: bool, get_pose: bool, view: sl.VIEW | None = None
    ) -> tuple[bool, bytes | None, dict | None, list[dict], dict | None]:
        """All ZED calls in one thread: grab + optional image + pose + objects + sensors."""
        if self.zed.grab(self.runtime) != sl.ERROR_CODE.SUCCESS:
            return False, None, None, [], None

        # --- pose (position + orientation) ---
        # FIX: capture orientation quaternion in addition to translation,
        # and only return pose data when tracking state is confirmed OK.
        # Broadcasting [0,0,0] on tracking loss would flood clients with
        # bogus origin readings.
        pose_data: dict | None = None
        if get_pose:
            state = self.zed.get_position(self.pose, sl.REFERENCE_FRAME.WORLD)
            if state == sl.POSITIONAL_TRACKING_STATE.OK:
                t = self.pose.get_translation(sl.Translation()).get()
                o = self.pose.get_orientation(sl.Orientation()).get()
                pose_data = {
                    "position":    [round(float(t[0]), 4), round(float(t[1]), 4), round(float(t[2]), 4)],
                    "orientation": [round(float(o[0]), 4), round(float(o[1]), 4), round(float(o[2]), 4), round(float(o[3]), 4)],
                }
            # If state != OK we leave pose_data as None; the caller skips the broadcast.

        # --- object detection (before image encode so we can draw boxes) ---
        objects: list[dict] = []
        overlays: list[tuple] = []  # (corners_4x2, label, color_rgb)
        if self._od_enabled:
            if self.zed.retrieve_objects(self._objects, self._obj_rt) == sl.ERROR_CODE.SUCCESS:
                for obj in self._objects.object_list:
                    if obj.tracking_state != sl.OBJECT_TRACKING_STATE.OK:
                        continue
                    p, d = obj.position, obj.dimensions
                    label = str(obj.label).split(".")[-1]
                    conf  = round(float(obj.confidence), 1)
                    objects.append({
                        "id":         obj.id,
                        "label":      label,
                        "position":   [round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3)],
                        "dimensions": [round(float(d[0]), 3), round(float(d[1]), 3), round(float(d[2]), 3)],
                        "confidence": conf,
                    })
                    bb2d = np.asarray(obj.bounding_box_2d)
                    if bb2d is not None and bb2d.ndim == 2 and bb2d.shape[0] >= 4:
                        color = _OBJ_COLORS_RGB.get(label, (255, 255, 255))
                        overlays.append((bb2d, f"{label} {conf:.0f}%", color))

        # --- image + overlay encode ---
        frame: bytes | None = None
        if capture and self._mjpeg_queues:
            image_view = view or sl.VIEW.LEFT
            is_depth = (image_view == sl.VIEW.DEPTH)
            if self.zed.retrieve_image(self._mat, image_view) == sl.ERROR_CODE.SUCCESS:
                raw = self._mat.get_data()
                if raw is not None:
                    arr = np.asarray(raw)
                    if arr.ndim >= 3 and arr.size > 0:
                        if is_depth:
                            # DEPTH view is grayscale in BGRA — take the single channel
                            img = Image.fromarray(np.ascontiguousarray(arr[:, :, 0]), mode="L")
                        else:
                            img = Image.fromarray(np.ascontiguousarray(arr[:, :, 2::-1]))
                            if overlays:
                                draw = ImageDraw.Draw(img)
                                for corners, text, color in overlays:
                                    pts = [(int(corners[i, 0]), int(corners[i, 1])) for i in range(4)]
                                    draw.polygon(pts, outline=color)
                                    tx, ty = pts[0]
                                    draw.rectangle([tx, ty - 18, tx + len(text) * 8, ty], fill=color)
                                    draw.text((tx + 2, ty - 17), text, fill=(0, 0, 0), font=_LABEL_FONT)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=55)
                        frame = buf.getvalue()

        # --- sensors (IMU / barometer / temperature) ---
        sensor_data: dict | None = None
        try:
            if self.zed.get_sensors_data(self._sensors, sl.TIME_REFERENCE.IMAGE) == sl.ERROR_CODE.SUCCESS:
                imu = self._sensors.get_imu_data()
                accel = np.asarray(imu.get_linear_acceleration().get())
                motion_g = round(float(np.linalg.norm(accel)) / 9.81, 3) if accel.size >= 3 else 0.0
                baro = self._sensors.get_barometric_pressure_data()
                alt_m = round(float(baro.relative_altitude), 2)
                sensor_data = {"motion_g": motion_g, "altitude_m": alt_m}
                try:
                    t = self._sensors.get_temperature_data()
                    tl = t.temperature_list
                    sensor_data["temp_left_c"]  = round(float(tl[sl.SENSOR_LOCATION.ONBOARD_LEFT]),  1)
                    sensor_data["temp_right_c"] = round(float(tl[sl.SENSOR_LOCATION.ONBOARD_RIGHT]), 1)
                except Exception:
                    pass
        except Exception:
            pass

        return True, frame, pose_data, objects, sensor_data

    async def _push_mjpeg_frame(self, frame: bytes, path_suffix: str | None = None) -> None:
        chunk = (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
            + frame + b"\r\n"
        )
        for qid, q in list(self._mjpeg_queues.items()):
            if path_suffix and self._mjpeg_paths.get(qid) != path_suffix:
                continue
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def _stream_key_for_path(self, path: str) -> str:
        if path.endswith("/right.mjpg"):
            return "/right.mjpg"
        if path.endswith("/depth.mjpg"):
            return "/depth.mjpg"
        if path.endswith("/video.mjpg"):
            return "/video.mjpg"
        return "/left.mjpg"

    def _active_mjpeg_views(self) -> list[tuple[str, sl.VIEW]]:
        paths = {path for path in self._mjpeg_paths.values()}
        views: list[tuple[str, sl.VIEW]] = []
        if "/video.mjpg" in paths:
            views.append(("/video.mjpg", sl.VIEW.SIDE_BY_SIDE))
        if "/left.mjpg" in paths:
            views.append(("/left.mjpg", sl.VIEW.LEFT))
        if "/right.mjpg" in paths:
            views.append(("/right.mjpg", sl.VIEW.RIGHT))
        if "/depth.mjpg" in paths:
            views.append(("/depth.mjpg", sl.VIEW.DEPTH))
        return views

    async def _mjpeg_handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break

            parts = request_line.decode(errors="ignore").split()
            path = parts[1] if len(parts) >= 2 else "/"
            if not any(path.endswith(p) for p in ("/video.mjpg", "/left.mjpg", "/right.mjpg", "/depth.mjpg")):
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found")
                await writer.drain()
                return

            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: keep-alive\r\n\r\n"
            )
            await writer.drain()

            q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
            qid = id(writer)
            self._mjpeg_queues[qid] = q
            self._mjpeg_paths[qid] = self._stream_key_for_path(path)

            while not writer.is_closing():
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=10.0)
                    writer.write(chunk)
                    await writer.drain()
                except asyncio.TimeoutError:
                    # Keep connection alive during slow startup; break only on write error
                    try:
                        writer.write(b"--frame\r\n\r\n")
                        await writer.drain()
                    except Exception:
                        break

        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass
        finally:
            self._mjpeg_queues.pop(id(writer), None)
            self._mjpeg_paths.pop(id(writer), None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _find_floor_plane(self) -> float | None:
        try:
            plane = sl.Plane()
            reset_pose = sl.Pose()
            if self.zed.find_floor_plane(plane, reset_pose) == sl.ERROR_CODE.SUCCESS:
                centre = np.asarray(plane.get_center())
                return round(float(centre[1]), 3) if centre.size >= 3 else None
        except Exception as e:
            print(f"[warn] floor plane: {e}")
        return None

    # ----------------------------------------------------------------- mapping

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return

        message = json.dumps(payload)
        tasks = [client.send(message) for client in list(self.clients)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for client, result in zip(list(self.clients), results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    def _extract_mesh(self) -> dict[str, Any]:
        mesh = sl.Mesh()
        self.zed.extract_whole_spatial_map(mesh)

        if mesh.get_number_of_triangles() == 0:
            return {}

        # FIX: use MESH_FILTER.LOW rather than HIGH.
        # HIGH is more aggressive/slower and can cause frame hitches on Jetson
        # during the executor call. LOW matches the SDK tutorial recommendation
        # and is sufficient for removing duplicate vertices and degenerate faces.
        try:
            fparams = sl.MeshFilterParameters()
            fparams.set(sl.MESH_FILTER.LOW)
            mesh.filter(fparams, update_mesh=True)
        except Exception:
            pass

        verts = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.triangles, dtype=np.int32)

        if verts.size == 0 or faces.size == 0:
            return {}

        # Cap face count — subsample and remap vertices to keep indices valid
        if len(faces) > self.config.max_faces:
            step = max(1, len(faces) // self.config.max_faces)
            faces = faces[::step]
            used = np.unique(faces)
            remap = np.full(len(verts), -1, dtype=np.int32)
            remap[used] = np.arange(len(used), dtype=np.int32)
            verts = verts[used]
            faces = remap[faces]

        return {
            "vertices": verts.flatten().tolist(),
            "faces": faces.flatten().tolist(),
        }

    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        self.clients.add(websocket)

        await websocket.send(
            json.dumps(
                {
                    "type": "mapping_status",
                    "rover_id": self.config.rover_id,
                    "session_id": self.session_id,
                    "state": "idle",
                    "timestamp": asyncio.get_event_loop().time(),
                }
            )
        )

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                action = msg.get("action")
                if action == "start_mapping":
                    self.mapping_active = True
                    self._grab_count = 0
                    self.session_id = msg.get("session_id") or self.session_id
                    await self._broadcast(
                        {
                            "type": "mapping_status",
                            "rover_id": self.config.rover_id,
                            "session_id": self.session_id,
                            "state": "mapping",
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
                elif action == "stop_mapping":
                    self.mapping_active = False
                    await self._broadcast(
                        {
                            "type": "mapping_status",
                            "rover_id": self.config.rover_id,
                            "session_id": self.session_id,
                            "state": "stopped",
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
                elif action == "clear_mapping":
                    self.seq = 0
                    self._grab_count = 0
                    self.mapping_active = False

                    # FIX: disable and re-enable in the correct order per the docs:
                    # spatial_mapping first, then positional_tracking, then
                    # re-enable tracking before re-enabling spatial mapping.
                    # Previously only spatial mapping was cycled, which would leave
                    # the system in a broken state if tracking had been disrupted.
                    self.zed.disable_spatial_mapping()
                    self.zed.disable_positional_tracking()

                    tracking_params = sl.PositionalTrackingParameters()
                    tracking_params.enable_area_memory = True
                    tracking = self.zed.enable_positional_tracking(tracking_params)
                    if tracking != sl.ERROR_CODE.SUCCESS:
                        print(f"[warn] failed to re-enable positional tracking after clear: {tracking}")

                    self._enable_spatial_mapping()

                    await self._broadcast(
                        {
                            "type": "mapping_status",
                            "rover_id": self.config.rover_id,
                            "session_id": self.session_id,
                            "state": "cleared",
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
        finally:
            self.clients.discard(websocket)

    async def _stream_loop(self) -> None:
        loop = asyncio.get_event_loop()
        mjpeg_every = max(1, self.config.fps // 8)  # ~8 fps for MJPEG
        _grab_total = 0

        while True:
            try:
                do_capture = (_grab_total % mjpeg_every == 0)
                active_views = self._active_mjpeg_views()

                pose_data: dict | None = None
                objects: list[dict] = []
                sensor_data: dict | None = None
                grabbed_any = False

                for idx, (path_suffix, view) in enumerate(active_views):
                    grabbed, frame, pose, found_objects, snsr = await loop.run_in_executor(
                        None, self._grab_frame, do_capture, self.mapping_active, view
                    )
                    if not grabbed:
                        continue
                    grabbed_any = True
                    if idx == 0:
                        pose_data = pose
                        objects = found_objects
                        sensor_data = snsr
                    if frame is not None:
                        await self._push_mjpeg_frame(frame, path_suffix)

                if not grabbed_any:
                    await asyncio.sleep(0.01)
                    continue

                _grab_total += 1

                if objects and self.clients and _grab_total % 5 == 0:
                    await self._broadcast({
                        "type": "object_detections",
                        "rover_id": self.config.rover_id,
                        "objects": objects,
                        "timestamp": loop.time(),
                    })

                if sensor_data and self.clients and _grab_total % 30 == 0:
                    await self._broadcast({
                        "type": "sensor_snapshot",
                        "rover_id": self.config.rover_id,
                        **sensor_data,
                        "timestamp": loop.time(),
                    })

                if self.mapping_active:
                    self._grab_count += 1

                    # FIX: only broadcast pose when tracking state was OK (pose_data
                    # is None when tracking was lost), and include full 6-DOF orientation
                    # quaternion alongside position so clients can render rover heading.
                    if pose_data is not None:
                        await self._broadcast({
                            "type": "pose_update",
                            "rover_id": self.config.rover_id,
                            "session_id": self.session_id,
                            "frame": "world",
                            "position":    pose_data["position"],
                            "orientation": pose_data["orientation"],
                            "timestamp": loop.time(),
                        })

                    if self._grab_count % self.config.mesh_interval == 0:
                        mesh_data = await loop.run_in_executor(None, self._extract_mesh)
                        if mesh_data:
                            await self._broadcast({
                                "type": "map_chunk",
                                "rover_id": self.config.rover_id,
                                "session_id": self.session_id,
                                "chunk_seq": self.seq,
                                "frame": "world",
                                **mesh_data,
                                "timestamp": loop.time(),
                            })
                            self.seq += 1

                    if self._grab_count % 75 == 0 and self._grab_count > 0:
                        floor_y = await loop.run_in_executor(None, self._find_floor_plane)
                        if floor_y is not None:
                            await self._broadcast({
                                "type": "floor_plane",
                                "rover_id": self.config.rover_id,
                                "y": floor_y,
                            })
                else:
                    self._grab_count = 0

            except Exception as e:
                print(f"[warn] stream loop error: {e}")

    async def run(self) -> None:
        mjpeg_server = await asyncio.start_server(
            self._mjpeg_handler,
            self.config.mjpeg_host,
            self.config.mjpeg_port,
        )
        print(
            f"MJPEG listening on http://{self.config.mjpeg_host}:{self.config.mjpeg_port}/video.mjpg"
        )

        async with websockets.serve(self._handle_client, self.config.ws_host, self.config.ws_port, ping_interval=None):
            print(f"mapper listening on ws://{self.config.ws_host}:{self.config.ws_port}")
            async with mjpeg_server:
                await self._stream_loop()

    def close(self) -> None:
        # FIX: disable in the correct order per the docs:
        # spatial mapping first, then positional tracking, then close.
        self.zed.disable_spatial_mapping()
        self.zed.disable_positional_tracking()
        self.zed.close()


if __name__ == "__main__":
    mapper = JetsonMapper(MapperConfig())
    try:
        asyncio.run(mapper.run())
    finally:
        mapper.close()
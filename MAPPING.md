# Rover Mapping System

Real-time 3D mapping and object detection for the rover swarm, running on a Jetson Orin Nano.

## Architecture

```
jetson_mapper.py          ← runs on each rover (Jetson)
  ├─ ZED camera           ← stereo depth + IMU
  ├─ Spatial mapping      ← builds 3D mesh of environment
  ├─ Object detection     ← detects people, vehicles, etc. in real time
  ├─ MJPEG server :8001   ← live camera feed with detection overlays
  └─ WebSocket server :9001 ← streams pose, mesh, detections to UI

UI/static/js/mapping.js   ← Three.js 3D viewport in the browser
  ├─ Per-rover WebSocket sessions
  ├─ Mesh rendering (coloured per rover)
  ├─ Object detection boxes (coloured per class, fade after 3 s)
  └─ Rover position marker (sphere)
```

## Running

```bash
# Terminal 1 — rover control API
python3 RoverFastApiServer.py

# Terminal 2 — mapper (ZED camera must be connected)
python3 jetson_mapper.py

# Terminal 3 — web UI
cd UI && python3 app.py
```

Open `http://localhost:5050`, select a rover from the dropdown.

Mapper startup takes ~7 seconds while the ZED initialises tracking and spatial mapping.

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 5050 | HTTP | Flask UI |
| 8001 | HTTP (MJPEG) | Live camera stream with detection overlays |
| 8002 | HTTP | Rover control API (FastAPI) |
| 9001 | WebSocket | Mapper data stream (pose, mesh, detections) |

## Mapping Controls

| Button | Action |
|--------|--------|
| Start  | Begin spatial mapping and pose tracking |
| Stop   | Pause mapping (keeps existing map) |
| Clear  | Wipe map and restart |
| Merge All | Combine all rover meshes into one global mesh |

## Object Detection

Uses ZED's built-in `MULTI_CLASS_BOX_FAST` neural model running on the Jetson GPU.

Detected classes and their colours:

| Class | Colour |
|-------|--------|
| PERSON | Cyan |
| VEHICLE | Orange |
| ANIMAL | Green |
| BAG | Yellow |
| ELECTRONICS | Purple |
| FRUIT_VEGETABLE | Pink |
| SPORT | Mint |

Detections appear as:
- **Camera feed** — coloured polygon outline + label chip drawn on the MJPEG stream
- **3D map** — wireframe bounding box at the object's world position, label floating above

Objects are removed from the 3D map after 3 seconds without a detection update.

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAPPER_VOXEL_SIZE` | `0.15` | Mesh resolution in metres (larger = fewer triangles) |
| `MAPPER_RANGE_M` | `5.0` | Maximum mapping range in metres |
| `MAPPER_FPS` | `15` | Target grab rate |
| `MAPPER_MESH_INTERVAL` | `60` | Grabs between mesh extractions (~4 s) |
| `MAPPER_MAX_FACES` | `4000` | Hard cap on triangles sent per update |
| `MAPPER_WS_PORT` | `9001` | WebSocket port |
| `MAPPER_MJPEG_PORT` | `8001` | MJPEG stream port |
| `ROVER_ID` | `rover0` | Rover identifier broadcast in all messages |

## Known Limitations

- **Rotation drift** — rapid 180° turns cause pose drift due to visual feature loss mid-sweep. Area memory (`enable_area_memory = True`) is enabled to allow relocalization when the rover returns to a previously seen area.
- **NumPy / OpenCV ABI mismatch** — OpenCV 4.13.0 on this Jetson was compiled against NumPy 2.0.2 but the runtime has NumPy 1.26.4. PIL is used for all JPEG encoding instead of `cv2.imencode`.
- **Single camera per process** — the ZED camera can only be opened by one process. Do not run `ZedFastApiServer.py` at the same time as `jetson_mapper.py`.

---

## Problems Encountered and Fixes

### `cv2.imencode` crash — NumPy / OpenCV ABI mismatch
**Symptom:** `cv2.error: img is not a numpy array, neither a scalar` on every frame, even with a plain `np.zeros` array.  
**Cause:** OpenCV 4.13.0 installed on this Jetson was built by Stereolabs CI against NumPy 2.0.2 (Python 3.9). The runtime environment has NumPy 1.26.4 (Python 3.10). NumPy 2.0 made breaking ABI changes so cv2 cannot recognise 1.x array objects at the C level.  
**Fix:** Replaced all `cv2.imencode` calls with PIL (`Image.fromarray` + `img.save(buf, format="JPEG")`). PIL uses Python's buffer protocol rather than the NumPy C API, so it is unaffected by the version mismatch.

### ZED `get_data()` returning non-standard array (OpenCV crash before the above)
**Symptom:** `cv2.imencode` rejected the image even after `np.ascontiguousarray`.  
**Cause:** `sl.Mat.get_data()` returns a ZED-internal memory view object. Slicing it (e.g. `raw[:, :, :3]`) returns another ZED object, not a numpy array. Passing that to `cv2.imencode` failed.  
**Fix:** Call `np.asarray(raw)` first to materialise a proper numpy array before any slicing.

### Thread-safety crash — ZED buffer accessed across threads
**Symptom:** Sporadic `cv2.error` / garbled frames after switching `zed.grab()` to `asyncio.run_in_executor`.  
**Cause:** `grab()` ran in a thread-pool executor thread; `retrieve_image()` + `get_data()` ran in the asyncio event loop thread. The ZED SDK's internal frame buffer is not safe to read from a different thread than the one that called `grab()`.  
**Fix:** Combined `grab()`, `retrieve_image()`, `get_position()`, and `retrieve_objects()` into a single `_grab_frame()` method that runs entirely inside one executor call, keeping all ZED SDK access on the same thread.

### Stale `.pyc` cache serving old bytecode
**Symptom:** After editing `jetson_mapper.py`, the mapper kept crashing with tracebacks referencing the old method name `_grab_and_capture` and old line numbers.  
**Cause:** Python's `__pycache__/jetson_mapper.cpython-310.pyc` had a newer or equal timestamp to the source file (filesystem timestamp precision issue on the Jetson), so Python skipped recompilation and ran the stale bytecode.  
**Fix:** Deleted `__pycache__/jetson_mapper.cpython-310.pyc` to force recompilation on next run.

### MJPEG stream timing out and dropping the browser connection
**Symptom:** Camera feed appeared blank; the browser would connect then disconnect after ~5 seconds.  
**Cause:** The MJPEG handler had a 5-second `asyncio.wait_for` timeout on the frame queue. If `_capture_frame` returned `None` (e.g. during slow startup), no frames were pushed to the queue and the handler exited.  
**Fix:** Extended timeout to 10 seconds and changed timeout behaviour to send a keepalive `--frame` boundary instead of closing the connection.

### WebSocket disconnecting when mapper restarted
**Symptom:** After restarting `jetson_mapper.py`, the UI showed "No rovers connected" and never recovered without a full page refresh.  
**Cause:** No auto-reconnect logic in the browser. Once a WebSocket closes, the session stayed dead.  
**Fix:** Added a 3-second auto-reconnect on the `close` event in `mapping.js`. Also disabled WebSocket ping/pong (`ping_interval=None` on `websockets.serve`) to prevent the server from dropping connections during heavy ZED processing.

### Mapper crashing on mesh extraction (`get_number_of_vertices` missing)
**Symptom:** `[warn] mesh extract/broadcast failed: 'pyzed.sl.Mesh' object has no attribute 'get_number_of_vertices'`  
**Cause:** The ZED SDK version on this Jetson exposes `get_number_of_triangles()` not `get_number_of_vertices()`.  
**Fix:** Changed the empty-mesh check to use `mesh.get_number_of_triangles() == 0`.

### Pose marker shooting to wrong position on 180° rotation
**Symptom:** The yellow rover dot on the map flies far off when rotating 180° on the spot.  
**Cause:** Visual-inertial odometry loses all tracked features mid-sweep (~90–135° into the turn). The tracker falls back to pure IMU integration; accelerometer double-integration drifts meters in under 2 seconds with no visual correction. No loop closure meant the tracker never snapped back when familiar terrain re-entered the frame.  
**Fix:** Enabled `PositionalTrackingParameters.enable_area_memory = True`. This saves visual descriptors of visited locations and triggers relocalization when the rover re-enters a previously mapped area, correcting accumulated drift.

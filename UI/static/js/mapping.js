(function () {
  const statusEl   = document.getElementById("mapping-status");
  const viewportEl = document.getElementById("mapping-viewport");
  const startBtn   = document.getElementById("mapping-start-btn");
  const stopBtn    = document.getElementById("mapping-stop-btn");
  const clearBtn   = document.getElementById("mapping-clear-btn");

  if (!statusEl || !viewportEl || !startBtn || !stopBtn || !clearBtn) {
    return;
  }

  if (!window.THREE) {
    statusEl.textContent = "Mapping unavailable: Three.js failed to load.";
    statusEl.classList.add("error");
    return;
  }

  const THREE = window.THREE;

  // One solid colour per rover index (cycled for >4 rovers)
  const ROVER_COLORS = [0x4a9eff, 0xff8c4a, 0x4aff8c, 0xd44aff];

  // Object detection — colour and display label per class
  const OBJ_COLORS = {
    PERSON:          0x00e5ff,
    VEHICLE:         0xff6d00,
    ANIMAL:          0x76ff03,
    BAG:             0xffea00,
    ELECTRONICS:     0xe040fb,
    FRUIT_VEGETABLE: 0xff4081,
    SPORT:           0x69f0ae,
  };
  const DEFAULT_OBJ_COLOR = 0xffffff;
  const TRACKING_STALE_MS = 3500;

  function makeObjectLabel(text, color) {
    const canvas = document.createElement("canvas");
    canvas.width = 256; canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = `#${color.toString(16).padStart(6, "0")}`;
    ctx.font = "bold 28px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 128, 32);
    const tex = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(0.6, 0.15, 1);
    return sprite;
  }

  function makeObjectBox(dims, color) {
    const [w, h, d] = dims.map((v) => Math.max(v, 0.1));
    const edges = new THREE.EdgesGeometry(new THREE.BoxGeometry(w, h, d));
    const mat = new THREE.LineBasicMaterial({ color, linewidth: 1 });
    return new THREE.LineSegments(edges, mat);
  }

  function makeRoverMarker(roverIdx) {
    const markerColors = [0xffb84d, 0x4dffd2, 0xff4d4d, 0xd24dff];
    const color = markerColors[roverIdx % markerColors.length];

    const group = new THREE.Group();

    const body = new THREE.Mesh(
      new THREE.SphereGeometry(0.07, 16, 16),
      new THREE.MeshStandardMaterial({ color })
    );

    // Cone points along +Y by default; rotate so forward points +Z.
    const heading = new THREE.Mesh(
      new THREE.ConeGeometry(0.04, 0.14, 12),
      new THREE.MeshStandardMaterial({ color, emissive: 0x101010 })
    );
    heading.rotation.x = Math.PI / 2;
    heading.position.set(0, 0, 0.12);

    group.add(body);
    group.add(heading);
    return group;
  }

  // ---------------------------------------------------------- scene globals

  const scene = {
    three:    null,
    camera:   null,
    renderer: null,
    controls: null,
    grid:     null,
  };

  // Per-rover session keyed by rover.name
  // Entry: { rover, ws, layers, trailPoints, trailLine, marker, roverIdx, connected, lastVertices, lastFaces }
  const sessions = new Map();

  // Global merge state
  let globalMesh = null;
  let mergedView = false;

  let activeRover = null;
  let globalSeq   = 0;
  let sessionId   = `session-${Date.now()}`;

  // ---------------------------------------------------------- status

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.classList.remove("ok", "error");
    if (kind) statusEl.classList.add(kind);
  }

  function updateStatusFromSessions() {
    const connected = [...sessions.values()].filter((s) => s.connected);
    if (!connected.length) {
      setStatus("No rovers connected.", "error");
      return;
    }
    const names = connected.map((s) => s.rover.name).join(", ");
    setStatus(`Connected: ${names}`, "ok");
  }

  function checkTrackingFreshness() {
    const now = Date.now();
    const stale = [...sessions.values()]
      .filter((s) => s.connected && s.mappingState === "mapping")
      .filter((s) => now - s.lastPoseAt > TRACKING_STALE_MS)
      .map((s) => s.rover.name);

    if (stale.length > 0) {
      setStatus(`Tracking lost/stale pose: ${stale.join(", ")}`, "error");
      return;
    }
  }

  // ---------------------------------------------------------- mesh

  function updateRoverMesh(session, vertices, faces) {
    if (vertices.length === 0 || faces.length === 0) return;

    if (session.layers) {
      const { face, edges, points } = session.layers;
      scene.three.remove(face);
      scene.three.remove(edges);
      scene.three.remove(points);
      face.geometry.dispose();
      face.material.dispose();
      edges.geometry.dispose();
      edges.material.dispose();
      points.geometry.dispose();
      points.material.dispose();
      session.layers = null;
    }

    const color = ROVER_COLORS[session.roverIdx % ROVER_COLORS.length];

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(vertices, 3));
    geometry.setIndex(new THREE.BufferAttribute(faces, 1));
    geometry.computeVertexNormals();

    const face = new THREE.Mesh(geometry, new THREE.MeshLambertMaterial({
      color,
      transparent: true,
      opacity: 0.08,
      side: THREE.DoubleSide,
    }));

    const edgeGeo = new THREE.EdgesGeometry(geometry);
    const edges = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.7,
    }));

    const pointGeo = new THREE.BufferGeometry();
    pointGeo.setAttribute("position", new THREE.BufferAttribute(vertices.slice(), 3));
    const points = new THREE.Points(pointGeo, new THREE.PointsMaterial({
      color,
      size: 0.02,
      transparent: true,
      opacity: 0.9,
    }));

    scene.three.add(face);
    scene.three.add(edges);
    scene.three.add(points);

    session.layers = { face, edges, points };
    session.lastVertices = vertices;
    session.lastFaces    = faces;
  }

  function clearRoverMesh(session) {
    if (session.layers) {
      const { face, edges, points } = session.layers;
      scene.three.remove(face);
      scene.three.remove(edges);
      scene.three.remove(points);
      face.geometry.dispose();
      face.material.dispose();
      edges.geometry.dispose();
      edges.material.dispose();
      points.geometry.dispose();
      points.material.dispose();
      session.layers = null;
    }
    if (session.trailLine) {
      scene.three.remove(session.trailLine);
      session.trailLine.geometry.dispose();
      session.trailLine.material.dispose();
      session.trailLine = null;
    }
    session.trailPoints  = [];
    session.lastVertices = null;
    session.lastFaces    = null;
    clearObjectMeshes(session);
  }

  function clearObjectMeshes(session) {
    for (const { box, label } of session.objectMeshes.values()) {
      scene.three.remove(box);
      scene.three.remove(label);
      box.geometry.dispose();
      box.material.dispose();
      label.material.map.dispose();
      label.material.dispose();
    }
    session.objectMeshes.clear();
  }

  function updateTrail(session, px, py, pz) {
    session.trailPoints.push(px, py, pz);
    if (session.trailPoints.length > 1500) {
      session.trailPoints.splice(0, 3);
    }
    if (session.trailLine) {
      scene.three.remove(session.trailLine);
      session.trailLine.geometry.dispose();
      session.trailLine.material.dispose();
      session.trailLine = null;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(session.trailPoints), 3));
    const color = ROVER_COLORS[session.roverIdx % ROVER_COLORS.length];
    session.trailLine = new THREE.Line(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.6 }));
    scene.three.add(session.trailLine);
  }

  function updateObjectDetections(session, objects) {
    const now = Date.now();
    const seenIds = new Set();

    for (const obj of objects) {
      seenIds.add(obj.id);
      const color = OBJ_COLORS[obj.label] ?? DEFAULT_OBJ_COLOR;
      const [px, py, pz] = obj.position;

      if (session.objectMeshes.has(obj.id)) {
        const entry = session.objectMeshes.get(obj.id);
        entry.box.position.set(px, py, pz);
        entry.label.position.set(px, py + (obj.dimensions[1] / 2) + 0.15, pz);
        entry.lastSeen = now;
      } else {
        const box   = makeObjectBox(obj.dimensions, color);
        const label = makeObjectLabel(obj.label, color);
        box.position.set(px, py, pz);
        label.position.set(px, py + (obj.dimensions[1] / 2) + 0.15, pz);
        scene.three.add(box);
        scene.three.add(label);
        session.objectMeshes.set(obj.id, { box, label, lastSeen: now });
      }
    }

    // Remove objects not seen for >3 s
    for (const [id, entry] of session.objectMeshes) {
      if (!seenIds.has(id) && now - entry.lastSeen > 800) {
        scene.three.remove(entry.box);
        scene.three.remove(entry.label);
        entry.box.geometry.dispose();
        entry.box.material.dispose();
        entry.label.material.map.dispose();
        entry.label.material.dispose();
        session.objectMeshes.delete(id);
      }
    }
  }

  // ---------------------------------------------------------- global merge

  function mergeAllMeshes() {
    const vertArrays = [];
    const faceArrays = [];
    let vertOffset = 0;

    for (const session of sessions.values()) {
      if (!session.lastVertices || session.lastVertices.length === 0) continue;
      vertArrays.push(session.lastVertices);

      const offsetFaces = new Uint32Array(session.lastFaces.length);
      for (let i = 0; i < session.lastFaces.length; i += 1) {
        offsetFaces[i] = session.lastFaces[i] + vertOffset;
      }
      faceArrays.push(offsetFaces);
      vertOffset += session.lastVertices.length / 3;
    }

    if (vertArrays.length === 0) {
      setStatus("No mapped data available to merge yet.", "error");
      return;
    }

    const totalVLen  = vertArrays.reduce((s, a) => s + a.length, 0);
    const totalFLen  = faceArrays.reduce((s, a) => s + a.length, 0);
    const mergedVerts = new Float32Array(totalVLen);
    const mergedFaces = new Uint32Array(totalFLen);

    let vo = 0;
    for (const v of vertArrays) { mergedVerts.set(v, vo); vo += v.length; }
    let fo = 0;
    for (const f of faceArrays) { mergedFaces.set(f, fo); fo += f.length; }

    if (globalMesh) {
      scene.three.remove(globalMesh);
      globalMesh.geometry.dispose();
      globalMesh.material.dispose();
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(mergedVerts, 3));
    geometry.setIndex(new THREE.BufferAttribute(mergedFaces, 1));
    geometry.computeVertexNormals();

    globalMesh = new THREE.Mesh(
      geometry,
      new THREE.MeshLambertMaterial({ color: 0xe0e8ff, side: THREE.DoubleSide })
    );
    scene.three.add(globalMesh);

    // Fade individual rover layers so the global mesh reads clearly
    for (const session of sessions.values()) {
      if (session.layers) {
        session.layers.face.material.opacity  = 0.03;
        session.layers.edges.material.opacity = 0.2;
        session.layers.points.material.opacity = 0.2;
      }
    }

    mergedView = true;
    const totalVerts = mergedVerts.length / 3;
    const totalFaces = mergedFaces.length / 3;
    setStatus(
      `Global mesh: ${sessions.size} rovers · ${totalVerts} verts · ${totalFaces} faces`,
      "ok"
    );
  }

  function unmerge() {
    if (globalMesh) {
      scene.three.remove(globalMesh);
      globalMesh.geometry.dispose();
      globalMesh.material.dispose();
      globalMesh = null;
    }
    for (const session of sessions.values()) {
      if (session.layers) {
        session.layers.face.material.opacity  = 0.08;
        session.layers.edges.material.opacity = 0.7;
        session.layers.points.material.opacity = 0.9;
      }
    }
    mergedView = false;
    updateStatusFromSessions();
  }

  // ---------------------------------------------------------- WebSocket per rover

  function onRoverMessage(session, data) {
    let msg;
    try { msg = JSON.parse(data); } catch (_) { return; }
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "mapping_status") {
      if (typeof msg.state === "string") {
        session.mappingState = msg.state;
        if (msg.state === "mapping" && !session.lastPoseAt) {
          session.lastPoseAt = Date.now();
        }
      }
      updateStatusFromSessions();
      return;
    }

    if (msg.type === "pose_update") {
      session.lastPoseAt = Date.now();
      const pos = Array.isArray(msg.position) ? msg.position : [0, 0, 0];
      const px = Number(pos[0] || 0);
      const py = Number(pos[1] || 0);
      const pz = Number(pos[2] || 0);
      session.marker.position.set(px, py, pz);
      updateTrail(session, px, py, pz);

      const orientation = Array.isArray(msg.orientation) ? msg.orientation : null;
      if (orientation && orientation.length === 4) {
        const qx = Number(orientation[0]);
        const qy = Number(orientation[1]);
        const qz = Number(orientation[2]);
        const qw = Number(orientation[3]);

        if (
          Number.isFinite(qx) &&
          Number.isFinite(qy) &&
          Number.isFinite(qz) &&
          Number.isFinite(qw)
        ) {
          session.marker.quaternion.set(qx, qy, qz, qw);
        }
      }
      return;
    }

    if (msg.type === "sensor_snapshot") {
      if (window.RoverSensors && typeof window.RoverSensors.update === "function") {
        window.RoverSensors.update(msg);
      }
      return;
    }

    if (msg.type === "floor_plane") {
      const y = Number(msg.y || 0);
      if (scene.grid) scene.grid.position.y = y;
      return;
    }

    if (msg.type === "object_detections") {
      updateObjectDetections(session, msg.objects || []);
      return;
    }

    if (msg.type === "map_chunk") {
      if (
        !Array.isArray(msg.vertices) ||
        !Array.isArray(msg.faces) ||
        msg.vertices.length === 0
      ) {
        return;
      }
      const vertices = new Float32Array(msg.vertices);
      const faces    = new Uint32Array(msg.faces);
      updateRoverMesh(session, vertices, faces);

      const vertCount = vertices.length / 3;
      const faceCount = faces.length / 3;
      setStatus(`${session.rover.name}: ${vertCount} verts / ${faceCount} faces`, "ok");
      return;
    }

    if (msg.type === "mapping_error") {
      setStatus(`${session.rover.name} error: ${msg.error || "unknown"}`, "error");
    }
  }

  function connectRover(rover, roverIdx) {
    if (!rover.mapper_ws_url) return;

    if (sessions.has(rover.name)) {
      const existing = sessions.get(rover.name);
      if (existing.ws && existing.ws.readyState < WebSocket.CLOSING) return;
    }

    const marker = makeRoverMarker(roverIdx);
    scene.three.add(marker);

    const session = {
      rover,
      ws: null,
      layers: null,
      trailPoints: [],
      trailLine: null,
      marker,
      roverIdx,
      connected: false,
      mappingState: "idle",
      lastPoseAt: Date.now(),
      lastVertices: null,
      lastFaces: null,
      objectMeshes: new Map(),
    };
    sessions.set(rover.name, session);

    const ws = new WebSocket(rover.mapper_ws_url);
    session.ws = ws;

    ws.addEventListener("open", () => {
      session.connected = true;
      updateStatusFromSessions();
    });

    ws.addEventListener("message", (event) => onRoverMessage(session, event.data));

    ws.addEventListener("close", () => {
      session.connected = false;
      session.mappingState = "disconnected";
      updateStatusFromSessions();
      // Auto-reconnect after 3 s
      setTimeout(() => {
        if (sessions.has(rover.name)) {
          session.ws = null;
          connectRover(rover, roverIdx);
        }
      }, 3000);
    });

    ws.addEventListener("error", () => {
      session.connected = false;
      session.mappingState = "error";
      updateStatusFromSessions();
    });
  }

  function disconnectRover(roverName) {
    const session = sessions.get(roverName);
    if (!session) return;
    if (session.ws) { session.ws.close(); session.ws = null; }
    clearRoverMesh(session);
    if (session.marker) {
      scene.three.remove(session.marker);
      session.marker.traverse((node) => {
        if (node.geometry && typeof node.geometry.dispose === "function") {
          node.geometry.dispose();
        }
        if (node.material) {
          if (Array.isArray(node.material)) {
            node.material.forEach((material) => material.dispose && material.dispose());
          } else if (typeof node.material.dispose === "function") {
            node.material.dispose();
          }
        }
      });
    }
    sessions.delete(roverName);
  }

  // ---------------------------------------------------------- commands

  function sendToActive(action, extra) {
    if (!activeRover) {
      setStatus("No active rover selected.", "error");
      return;
    }
    const session = sessions.get(activeRover.name);
    if (!session || !session.ws || session.ws.readyState !== WebSocket.OPEN) {
      setStatus(`Mapper not connected for ${activeRover.name}.`, "error");
      return;
    }
    const payload = {
      action,
      rover_id:   activeRover.name,
      session_id: sessionId,
      seq:        globalSeq,
      timestamp:  Date.now() / 1000,
      ...(extra || {}),
    };
    globalSeq += 1;
    session.ws.send(JSON.stringify(payload));
  }

  function clearActiveMap() {
    if (!activeRover) return;
    const session = sessions.get(activeRover.name);
    if (session) clearRoverMesh(session);
  }

  // ---------------------------------------------------------- scene

  function initScene() {
    const width  = viewportEl.clientWidth  || 640;
    const height = viewportEl.clientHeight || 320;

    scene.three = new THREE.Scene();
    scene.three.background = new THREE.Color(0x050b1e);

    scene.camera = new THREE.PerspectiveCamera(65, width / height, 0.01, 1000);
    scene.camera.position.set(0, 1.2, 2.5);

    scene.renderer = new THREE.WebGLRenderer({ antialias: true });
    scene.renderer.setSize(width, height);
    viewportEl.innerHTML = "";
    viewportEl.appendChild(scene.renderer.domElement);

    scene.grid = new THREE.GridHelper(6, 24, 0x2f7fd8, 0x1c355f);
    scene.three.add(scene.grid);
    scene.three.add(new THREE.AmbientLight(0xffffff, 0.5));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(2, 3, 4);
    scene.three.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.3);
    fill.position.set(-2, 1, -2);
    scene.three.add(fill);

    // Load OrbitControls dynamically (avoids an extra script tag in HTML)
    const orbitScript = document.createElement("script");
    orbitScript.src = "/static/js/OrbitControls.js";
    orbitScript.onload = function () {
      if (window.THREE && window.THREE.OrbitControls) {
        scene.controls = new THREE.OrbitControls(scene.camera, scene.renderer.domElement);
        scene.controls.enableDamping = true;
        scene.controls.dampingFactor = 0.05;
        scene.controls.target.set(0, 0, 0);
      }
    };
    document.head.appendChild(orbitScript);

    // Inject "Merge All / Unmerge" button alongside the existing controls
    const controlsEl = document.querySelector(".mapping-controls");
    if (controlsEl) {
      const mergeBtn = document.createElement("button");
      mergeBtn.id        = "mapping-merge-btn";
      mergeBtn.className = "btn btn-soft";
      mergeBtn.type      = "button";
      mergeBtn.textContent = "Merge All";
      mergeBtn.addEventListener("click", () => {
        if (mergedView) {
          unmerge();
          mergeBtn.textContent = "Merge All";
        } else {
          mergeAllMeshes();
          mergeBtn.textContent = "Unmerge";
        }
      });
      controlsEl.appendChild(mergeBtn);
    }

    function animate() {
      requestAnimationFrame(animate);
      if (scene.controls) scene.controls.update();
      scene.renderer.render(scene.three, scene.camera);
    }
    animate();

    window.addEventListener("resize", () => {
      const w = viewportEl.clientWidth  || 640;
      const h = viewportEl.clientHeight || 320;
      scene.camera.aspect = w / h;
      scene.camera.updateProjectionMatrix();
      scene.renderer.setSize(w, h);
    });
  }

  // ---------------------------------------------------------- public API

  function setRovers(roverList) {
    if (!Array.isArray(roverList)) return;

    const incoming = new Set(roverList.map((r) => r.name));
    for (const name of [...sessions.keys()]) {
      if (!incoming.has(name)) disconnectRover(name);
    }

    roverList.forEach((rover, idx) => {
      if (!sessions.has(rover.name)) connectRover(rover, idx);
    });
  }

  function setActiveRover(rover) {
    activeRover = rover || null;
    sessionId   = `session-${Date.now()}`;

    if (!activeRover) {
      setStatus("No rover selected.", "error");
      return;
    }

    if (!sessions.has(activeRover.name)) {
      connectRover(activeRover, sessions.size);
    }

    updateStatusFromSessions();
  }

  // ---------------------------------------------------------- button wiring

  startBtn.addEventListener("click", () => {
    sessionId = `session-${Date.now()}`;
    sendToActive("start_mapping");
  });
  stopBtn.addEventListener("click",  () => sendToActive("stop_mapping"));
  clearBtn.addEventListener("click", () => {
    sendToActive("clear_mapping");
    clearActiveMap();
    setStatus("Map cleared.", "ok");
  });

  setInterval(checkTrackingFreshness, 1200);

  initScene();

  window.RoverMapping = {
    setActiveRover,
    setRovers,
  };
})();

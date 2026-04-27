(function () {
  const initial = window.__INITIAL_STATE__ || { rovers: [], activeRover: null };

  const roverTabs = document.getElementById("rover-tabs");
  const sshBtn = document.getElementById("ssh-btn");
  const rescanBtn = document.getElementById("rescan-btn");
  const rescanMessage = document.getElementById("rescan-message");
  const refreshHealthBtn = document.getElementById("refresh-health-btn");
  const executeBtn = document.getElementById("execute-btn");
  const codeInput = document.getElementById("code-input");
  const timeoutInput = document.getElementById("timeout-seconds");

  const modalOverlay = document.getElementById("modal-overlay");
  const modalTitle = document.getElementById("modal-title");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.getElementById("modal-close");

  const aiModalOverlay = document.getElementById("ai-modal-overlay");
  const aiModalTitle = document.getElementById("ai-modal-title");
  const aiModalBody = document.getElementById("ai-modal-body");
  const aiModalClose = document.getElementById("ai-modal-close");
  const aiAssistBtn = document.getElementById("ai-assist-btn");
  const aiGenerateBtn = document.getElementById("ai-generate-btn");
  const aiExecuteBtn = document.getElementById("ai-execute-btn");
  const aiCopyBtn = document.getElementById("ai-copy-btn");
  const aiPromptInput = document.getElementById("ai-prompt-input");
  const aiGeneratedCode = document.getElementById("ai-generated-code");
  const aiMessage = document.getElementById("ai-message");

  const cameraRoverName = document.getElementById("camera-rover-name");
  const cameraStreamImg = document.getElementById("camera-stream-img");
  const streamButtons = Array.from(document.querySelectorAll(".stream-btn"));
  const healthSummary = document.getElementById("health-summary");
  const healthRoverName = document.getElementById("health-rover-name");
  const healthMessage = document.getElementById("health-message");
  const executeMessage = document.getElementById("execute-message");
  const executionOutput = document.getElementById("execution-output");

  const state = {
    rovers: Array.isArray(initial.rovers) ? [...initial.rovers] : [],
    healthByRover: {},
    activeHealth: null,
    healthRequestInFlight: false,
  };

  let activeRover = initial.activeRover;
  let selectedStream = "video";

  const STREAM_PATHS = {
    video: "video.mjpg",
    left: "left.mjpg",
    right: "right.mjpg",
    depth: "depth.mjpg",
  };

  function setMessage(element, text, kind) {
    element.textContent = text || "";
    element.classList.remove("ok", "error");
    if (kind) {
      element.classList.add(kind);
    }
  }

  function setRescanMessage(text, kind) {
    setMessage(rescanMessage, text, kind);
  }

  function friendlyError(rawError, contextLabel) {
    const source = String(rawError || "").toLowerCase();

    if (
      source.includes("failed to resolve") ||
      source.includes("nameresolutionerror") ||
      source.includes("getaddrinfo failed")
    ) {
      return `${contextLabel}: Rover hostname/IP cannot be resolved. You are likely not connected to the rover network. Connect to the rover network or add/select a direct rover IP.`;
    }

    if (
      source.includes("max retries exceeded") ||
      source.includes("connection refused") ||
      source.includes("failed to establish a new connection") ||
      source.includes("connection aborted")
    ) {
      return `${contextLabel}: Rover is unreachable right now. Check Wi-Fi/network connection, rover power, and that rover services are running.`;
    }

    if (source.includes("timed out") || source.includes("read timed out")) {
      return `${contextLabel}: Rover did not respond in time. If you're not connected to rover network, connect first and try again.`;
    }

    return `${contextLabel}: ${rawError || "Unknown error."}`;
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function showModal(title, bodyHtml) {
    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHtml;
    modalOverlay.classList.remove("hidden");
    modalOverlay.setAttribute("aria-hidden", "false");
  }

  function closeModal() {
    modalOverlay.classList.add("hidden");
    modalOverlay.setAttribute("aria-hidden", "true");
    modalBody.innerHTML = "";
  }

  function cameraStreamUrl(rover, streamKey) {
    if (!rover || !rover.host) {
      return "";
    }

    const cameraPort = rover.camera_port || 8001;
    const streamPath = STREAM_PATHS[streamKey] || STREAM_PATHS.video;
    return `http://${rover.host}:${cameraPort}/${streamPath}`;
  }

  function setCameraPlaceholder() {
    if (!activeRover) {
      cameraRoverName.textContent = "No rover selected.";
      if (cameraStreamImg) {
        cameraStreamImg.removeAttribute("src");
      }
      if (window.RoverMapping && typeof window.RoverMapping.setActiveRover === "function") {
        window.RoverMapping.setActiveRover(null);
      }
      return;
    }

    const streamPath = STREAM_PATHS[selectedStream] || STREAM_PATHS.video;
    const streamUrl = cameraStreamUrl(activeRover, selectedStream);
    if (cameraStreamImg) {
      cameraStreamImg.src = streamUrl;
    }
    cameraRoverName.textContent = `Streaming ${streamPath} from ${activeRover.name} (${activeRover.host})`;
    if (window.RoverMapping && typeof window.RoverMapping.setActiveRover === "function") {
      window.RoverMapping.setActiveRover(activeRover);
    }
  }

  function setActiveStreamButton() {
    for (const button of streamButtons) {
      const isActive = button.dataset.stream === selectedStream;
      button.classList.toggle("active", isActive);
    }
  }

  function formatCpu(cpu) {
    if (!cpu) {
      return "--";
    }

    const one = cpu["1min"] ?? "--";
    const five = cpu["5min"] ?? "--";
    const fifteen = cpu["15min"] ?? "--";
    return `${one} / ${five} / ${fifteen}`;
  }

  function formatMemory(memory) {
    if (!memory) {
      return "--";
    }

    const used = memory.used ?? "--";
    const total = memory.total ?? "--";
    const free = memory.free ?? "--";
    return `${used} used / ${total} total (free: ${free})`;
  }

  function formatRamMini(memory) {
    if (!memory) {
      return "--";
    }

    const used = memory.used ?? "--";
    const total = memory.total ?? "--";
    return `${used}/${total}`;
  }

  function formatDisk(disk) {
    if (!disk) {
      return "--";
    }

    const mount = disk.mount ?? "?";
    const used = disk.used ?? "--";
    const size = disk.size ?? "--";
    const pct = disk.use_pct ?? "--";
    return `${mount}: ${used}/${size} (${pct})`;
  }

  function healthMiniText(record) {
    if (!record || !record.summary) {
      return "Temp: --  CPU: --  RAM: --";
    }

    if (!record.ok) {
      return "Temp: --  CPU: --  RAM: --";
    }

    const summary = record.summary || {};
    const temp =
      summary.max_temp_c !== null && summary.max_temp_c !== undefined
        ? `${summary.max_temp_c}°C`
        : "--";
    const cpu1 = summary.cpu_load && summary.cpu_load["1min"] !== undefined ? summary.cpu_load["1min"] : "--";
    const ram = formatRamMini(summary.memory);
    return `Temp: ${temp}  CPU: ${cpu1}  RAM: ${ram}`;
  }

  function renderActiveHealth() {
    const record = state.activeHealth;
    const rover = record?.rover || activeRover || {};

    if (!record) {
      const roverAvailable =
        !!activeRover && Array.isArray(state.rovers) && state.rovers.some((roverItem) => roverItem.name === activeRover.name);

      healthRoverName.textContent = roverAvailable
        ? `${activeRover.name} (${activeRover.host})`
        : "No rover connected.";
      healthSummary.innerHTML = '<p class="subtext">Refresh to load rover health.</p>';
      return;
    }

    healthRoverName.textContent = `${rover.name || "Selected rover"} (${rover.host || "--"})`;

    if (!record.ok) {
      healthSummary.innerHTML = `
        <div class="health-grid">
          <section class="health-card">
            <h3>Status</h3>
            <p class="health-value error">UNAVAILABLE</p>
            <p class="health-details">${escapeHtml(record.error || "Health check failed.")}</p>
          </section>
        </div>
      `;
      return;
    }

    const summary = record.summary || {};
    const tempVal = summary.max_temp_c !== null && summary.max_temp_c !== undefined ? summary.max_temp_c : null;
    const tempStr = tempVal !== null ? `${tempVal} °C` : "--";
    const tempClass = tempVal === null ? "" : tempVal > 75 ? "error" : tempVal > 60 ? "warn" : "ok";

    const cpuLoad = summary.cpu_load || {};
    const cpu1 = cpuLoad["1min"] !== undefined ? cpuLoad["1min"] : "--";
    const cpu5 = cpuLoad["5min"] !== undefined ? cpuLoad["5min"] : "--";
    const cpu15 = cpuLoad["15min"] !== undefined ? cpuLoad["15min"] : "--";

    const mem = summary.memory || {};
    const memUsed = mem.used || "--";
    const memTotal = mem.total || "--";
    const memFree = mem.free || "--";

    const disk = summary.disk || {};
    const diskMount = disk.mount || "/";
    const diskUsed = disk.used || "--";
    const diskSize = disk.size || "--";
    const diskPct = disk.use_pct || "--";

    const status = (summary.status || "unknown").toString().toUpperCase();
    const statusClass = status === "OK" ? "ok" : "error";

    healthSummary.innerHTML = `
      <div class="health-grid">
        <section class="health-card">
          <h3>Status</h3>
          <p class="health-value ${escapeHtml(statusClass)}">${escapeHtml(status)}</p>
          <p class="health-details">${escapeHtml(rover.name || "Selected rover")} · ${escapeHtml(rover.host || "--")}</p>
        </section>
        <section class="health-card">
          <h3>Max Temp</h3>
          <p class="health-value ${escapeHtml(tempClass)}">${escapeHtml(tempStr)}</p>
          <p class="health-details">Hottest sensor on the Jetson board</p>
        </section>
        <section class="health-card">
          <h3>CPU Load</h3>
          <p class="health-value">${escapeHtml(String(cpu1))}</p>
          <p class="health-details">1 min avg · 5 min: ${escapeHtml(String(cpu5))} · 15 min: ${escapeHtml(String(cpu15))}</p>
        </section>
        <section class="health-card">
          <h3>RAM</h3>
          <p class="health-value">${escapeHtml(memUsed)} <span class="health-total">/ ${escapeHtml(memTotal)}</span></p>
          <p class="health-details">Free: ${escapeHtml(memFree)}</p>
        </section>
        <section class="health-card">
          <h3>Disk <span class="health-mount">${escapeHtml(diskMount)}</span></h3>
          <p class="health-value">${escapeHtml(diskUsed)} <span class="health-total">/ ${escapeHtml(diskSize)}</span></p>
          <p class="health-details">Used: ${escapeHtml(diskPct)}</p>
        </section>
      </div>
    `;
  }

  async function beginInlineIpEdit(hostEl, rover) {
    if (!hostEl || !rover) {
      return;
    }

    if (hostEl.classList.contains("editing")) {
      return;
    }

    hostEl.classList.add("editing");
    const currentAlias = rover.ip_override || "";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "rover-tab-ip-input";
    input.placeholder = "No IP address";
    input.value = currentAlias;

    hostEl.innerHTML = "";
    hostEl.appendChild(input);
    input.focus();
    input.select();

    let committed = false;

    const commit = async () => {
      if (committed) {
        return;
      }
      committed = true;
      const nextValue = (input.value || "").trim();

      try {
        await setRoverIpAlias(rover.name, nextValue);
      } catch (error) {
        setMessage(healthMessage, friendlyError(error.message, "Unable to update rover IP alias"), "error");
      }
    };

    input.addEventListener("keydown", async (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        await commit();
      }
      if (event.key === "Escape") {
        committed = true;
        renderRoverTabs();
      }
    });

    input.addEventListener("blur", async () => {
      await commit();
    });
  }

  function renderRoverTabs() {
    roverTabs.innerHTML = "";

    if (!state.rovers.length) {
      roverTabs.innerHTML = '<p class="subtext">No rovers available from latest scan.</p>';
      activeRover = null;
      return;
    }

    for (const rover of state.rovers) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `rover-tab ${activeRover && activeRover.name === rover.name ? "active" : ""}`;
      button.setAttribute("role", "tab");
      button.dataset.roverName = rover.name;

      const summary = state.healthByRover[rover.name];
      const aliasText = rover.ip_override ? rover.ip_override : "No IP address";
      button.innerHTML = `
        <span class="rover-tab-title">${escapeHtml(rover.name)}</span>
        <span class="rover-tab-host" data-rover-name="${escapeHtml(rover.name)}">(${escapeHtml(aliasText)})</span>
        <span class="rover-tab-stats">${escapeHtml(healthMiniText(summary))}</span>
      `;

      button.addEventListener("click", async () => {
        await switchRover(rover.name);
      });

      const hostLine = button.querySelector(".rover-tab-host");
      if (hostLine instanceof HTMLElement) {
        hostLine.addEventListener("click", async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await beginInlineIpEdit(hostLine, rover);
        });
      }

      roverTabs.appendChild(button);
    }

  }

  function renderExecutionResult(result) {
    executionOutput.textContent = JSON.stringify(result, null, 2);
  }

  async function refreshRoverHealthCache() {
    if (state.healthRequestInFlight) {
      return;
    }

    state.healthRequestInFlight = true;

    try {
      const response = await fetch("/api/health-all");
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Health request failed (${response.status})`);
      }

      const nextHealth = {};
      for (const entry of payload.results || []) {
        if (entry && entry.rover && entry.rover.name) {
          nextHealth[entry.rover.name] = entry;
        }
      }

      state.healthByRover = nextHealth;
      renderRoverTabs();
    } catch (error) {
      setMessage(healthMessage, friendlyError(error.message, "Unable to refresh rover stats"), "error");
    } finally {
      state.healthRequestInFlight = false;
    }
  }

  async function refreshActiveHealth(options) {
    const opts = options || {};
    const showNoRoverError = !!opts.showNoRoverError;

    const canQueryActiveHealth =
      !!activeRover && Array.isArray(state.rovers) && state.rovers.some((rover) => rover.name === activeRover.name);

    if (!canQueryActiveHealth) {
      state.activeHealth = null;
      renderActiveHealth();
      if (showNoRoverError) {
        setMessage(healthMessage, "No rover connected. Rescan devices and select a rover.", "error");
      } else {
        setMessage(healthMessage, "", "");
      }
      return;
    }

    setMessage(healthMessage, "Fetching rover health…");

    try {
      const response = await fetch("/api/health");
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Health request failed (${response.status})`);
      }

      state.activeHealth = payload;
      activeRover = payload.rover || activeRover;
      renderActiveHealth();
      renderRoverTabs();
      setMessage(healthMessage, `Health updated for ${payload.rover.name}.`, "ok");
    } catch (error) {
      state.activeHealth = null;
      renderActiveHealth();
      setMessage(healthMessage, friendlyError(error.message, "Health unavailable"), "error");
    }
  }

  async function switchRover(roverName) {
    setMessage(healthMessage, "Switching rover…");

    try {
      const response = await fetch("/api/select-rover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rover_name: roverName }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Switch failed (${response.status})`);
      }

      activeRover = payload.active_rover;
      renderRoverTabs();
      setCameraPlaceholder();
      setMessage(healthMessage, `Active rover: ${activeRover.name}`, "ok");
      await refreshActiveHealth();
    } catch (error) {
      setMessage(healthMessage, friendlyError(error.message, "Unable to switch rover"), "error");
    }
  }

  async function refreshRoversFromServer() {
    const response = await fetch("/api/rovers");
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Unable to refresh rover list (${response.status})`);
    }

    state.rovers = Array.isArray(payload.rovers) ? payload.rovers : [];
    activeRover = payload.active_rover || activeRover;
    window.RoverMapping?.setRovers(state.rovers);
    renderRoverTabs();
  }

  async function setRoverIpAlias(roverName, ipAddress) {
    const response = await fetch("/api/add-rover-ip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rover_name: roverName, ip_address: ipAddress }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Unable to add IP (${response.status})`);
    }

    state.rovers = Array.isArray(payload.rovers) ? payload.rovers : state.rovers;
    activeRover = payload.active_rover || activeRover;
    renderRoverTabs();
    setCameraPlaceholder();
    window.RoverMapping?.setRovers(state.rovers);

    setMessage(healthMessage, payload.message || "Rover IP alias updated.", "ok");
    await refreshRoverHealthCache();
    await refreshActiveHealth();
  }

  async function scanRovers(triggeredByRescan) {
    const response = await fetch("/api/scan-rovers");
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Scan failed (${response.status})`);
    }

    state.rovers = Array.isArray(payload.rovers) ? payload.rovers : state.rovers;
    activeRover = payload.active_rover || activeRover;
    if (!state.rovers.length) {
      activeRover = null;
      state.activeHealth = null;
    }
    window.RoverMapping?.setRovers(state.rovers);
    renderRoverTabs();
    setCameraPlaceholder();

    const discovered = payload.discovered || [];
    if (triggeredByRescan) {
      const label = discovered.length ? discovered.join(", ") : "none";
      setRescanMessage(`Rescan complete. Visible rovers: ${label}.`, "ok");
    }
  }

  async function executeCode() {
    if (!activeRover) {
      setMessage(executeMessage, "No visible rover selected. Rescan and choose a rover first.", "error");
      return;
    }

    const code = codeInput.value || "";
    const timeoutSeconds = Number(timeoutInput.value || 60);

    if (!code.trim()) {
      setMessage(executeMessage, "Write some Python code first.", "error");
      return;
    }

    setMessage(executeMessage, `Executing on ${activeRover?.name || "rover"}…`);
    executeBtn.disabled = true;

    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          timeout_seconds: timeoutSeconds,
        }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Execution failed (${response.status})`);
      }

      renderExecutionResult(payload.result);
      const statusKind = payload.result.ok ? "ok" : "error";
      setMessage(executeMessage, `Execution complete on ${payload.rover.name}.`, statusKind);
    } catch (error) {
      setMessage(executeMessage, friendlyError(error.message, "Execution failed"), "error");
      executionOutput.textContent = "No execution result received due to request error.";
    } finally {
      executeBtn.disabled = false;
    }
  }

  async function showSshHelp() {
    if (!state.rovers.length) {
      setRescanMessage("No rover connected. Rescan devices and select a rover.", "error");
      return;
    }

    try {
      const response = await fetch("/api/ssh-instructions");

      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Unable to load SSH help (${response.status})`);
      }

      const stepsHtml = (payload.steps || [])
        .map((step) => `<li>${escapeHtml(step)}</li>`)
        .join("");

      showModal(
        `SSH Instructions for ${payload.rover?.name || "active rover"}`,
        `
          <p class="subtext">Follow these exact steps in order.</p>
          <ol class="modal-steps">${stepsHtml}</ol>
          <div class="modal-command-block">
            <p class="subtext">Command to copy:</p>
            <pre>${escapeHtml(payload.command || "")}</pre>
          </div>
        `
      );
    } catch (error) {
      setMessage(healthMessage, friendlyError(error.message, "Unable to load SSH help"), "error");
    }
  }

  function closeAiModal() {
    aiModalOverlay.classList.add("hidden");
    aiModalOverlay.setAttribute("aria-hidden", "true");
  }

  function openAiModal() {
    aiModalOverlay.classList.remove("hidden");
    aiModalOverlay.setAttribute("aria-hidden", "false");
    aiPromptInput.focus();
  }

  async function generateAiCode() {
    const prompt = (aiPromptInput.value || "").trim();
    
    if (!prompt) {
      setMessage(aiMessage, "Please enter a prompt.", "error");
      return;
    }

    setMessage(aiMessage, "Generating code with AI...");
    aiGenerateBtn.disabled = true;
    aiGeneratedCode.textContent = "";

    try {
      const response = await fetch("/api/ai_command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: prompt,
          history: [],
        }),
      });

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let generatedCode = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        
        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i];
          if (line.startsWith("data: ")) {
            const data = line.slice(6);
            if (data) {
              try {
                const json = JSON.parse(data);
                if (json.type === "content") {
                  generatedCode += json.content || "";
                  aiGeneratedCode.textContent = generatedCode;
                  aiGeneratedCode.scrollTop = aiGeneratedCode.scrollHeight;
                } else if (json.type === "result") {
                  generatedCode = json.code || generatedCode;
                  aiGeneratedCode.textContent = generatedCode;
                } else if (json.type === "error") {
                  throw new Error(json.error || "Unknown error during generation");
                }
              } catch (e) {
                if (!e.message.includes("JSON")) {
                  throw e;
                }
              }
            }
          }
        }

        buffer = lines[lines.length - 1];
      }

      if (generatedCode) {
        setMessage(aiMessage, "Code generated successfully!", "ok");
        aiExecuteBtn.disabled = false;
        aiCopyBtn.disabled = false;
      } else {
        setMessage(aiMessage, "No code was generated. Try a different prompt.", "error");
      }
    } catch (error) {
      setMessage(aiMessage, `Generation failed: ${error.message}`, "error");
      aiGeneratedCode.textContent = `Error: ${error.message}`;
    } finally {
      aiGenerateBtn.disabled = false;
    }
  }

  function copyAiCode() {
    const code = aiGeneratedCode.textContent || "";
    if (!code) {
      setMessage(aiMessage, "No code to copy.", "error");
      return;
    }

    navigator.clipboard.writeText(code).then(() => {
      setMessage(aiMessage, "Code copied to clipboard!", "ok");
    }).catch((err) => {
      setMessage(aiMessage, `Failed to copy: ${err.message}`, "error");
    });
  }

  async function executeAiCode() {
    const code = aiGeneratedCode.textContent || "";
    if (!code || !code.trim()) {
      setMessage(aiMessage, "No code to execute.", "error");
      return;
    }

    // Close the AI modal
    closeAiModal();

    // Put the code in the code editor
    codeInput.value = code;

    // Execute the code
    await executeCode();
  }

  function wireEvents() {
    rescanBtn.addEventListener("click", async () => {
      setRescanMessage("Rescanning devices…");
      try {
        await scanRovers(true);
        await refreshRoverHealthCache();
        await refreshActiveHealth();
      } catch (error) {
        setRescanMessage(friendlyError(error.message, "Rescan failed"), "error");
      }
    });
    refreshHealthBtn.addEventListener("click", async () => {
      await refreshRoverHealthCache();
      await refreshActiveHealth({ showNoRoverError: true });
    });
    executeBtn.addEventListener("click", executeCode);
    sshBtn.addEventListener("click", showSshHelp);

    for (const button of streamButtons) {
      button.addEventListener("click", () => {
        const stream = button.dataset.stream || "video";
        selectedStream = STREAM_PATHS[stream] ? stream : "video";
        setActiveStreamButton();
        setCameraPlaceholder();
      });
    }

    cameraStreamImg.addEventListener("error", () => {
      cameraRoverName.textContent = `Stream unavailable (${STREAM_PATHS[selectedStream] || STREAM_PATHS.video}). You're likely not connected to rover network, or camera service is offline on ${activeRover?.host || "selected rover"}:8001.`;
    });

    modalClose.addEventListener("click", closeModal);
    modalOverlay.addEventListener("click", (event) => {
      if (event.target === modalOverlay) {
        closeModal();
      }
    });

    aiAssistBtn.addEventListener("click", openAiModal);
    aiModalClose.addEventListener("click", closeAiModal);
    aiModalOverlay.addEventListener("click", (event) => {
      if (event.target === aiModalOverlay) {
        closeAiModal();
      }
    });
    aiGenerateBtn.addEventListener("click", generateAiCode);
    aiExecuteBtn.addEventListener("click", executeAiCode);
    aiCopyBtn.addEventListener("click", copyAiCode);
    aiPromptInput.addEventListener("keydown", async (event) => {
      if (event.key === "Enter" && event.ctrlKey) {
        await generateAiCode();
      }
    });
  }

  async function start() {
    try {
      await refreshRoversFromServer();
    } catch (error) {
      setMessage(healthMessage, friendlyError(error.message, "Could not refresh rover list"), "error");
    }

    renderRoverTabs();
    setActiveStreamButton();
    setCameraPlaceholder();
    window.RoverMapping?.setRovers(state.rovers);
    wireEvents();

    try {
      await scanRovers(false);
    } catch (error) {
      setMessage(healthMessage, friendlyError(error.message, "Initial rover scan failed"), "error");
    }

    await refreshRoverHealthCache();
    await refreshActiveHealth();
    setInterval(async () => {
      await refreshRoverHealthCache();
      await refreshActiveHealth();
    }, 10000);
  }

  window.RoverSensors = {
    update(snap) {
      const section = document.getElementById("health-zed-section");
      if (!section) return;
      section.style.display = "";

      const tl = snap.temp_left_c  != null ? `${snap.temp_left_c} °C`  : "--";
      const tr = snap.temp_right_c != null ? `${snap.temp_right_c} °C` : "--";
      const alt = snap.altitude_m  != null ? `${snap.altitude_m} m`    : "--";
      const mot = snap.motion_g    != null ? `${snap.motion_g} g`      : "--";

      const tempEl = document.getElementById("zed-temp-value");
      const altEl  = document.getElementById("zed-alt-value");
      const motEl  = document.getElementById("zed-motion-value");

      if (tempEl) tempEl.textContent = `${tl} / ${tr}`;
      if (altEl)  altEl.textContent  = alt;
      if (motEl)  motEl.textContent  = mot;
    },
  };

  start();
})();

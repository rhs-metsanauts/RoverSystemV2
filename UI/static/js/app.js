(function () {
  const initial = window.__INITIAL_STATE__ || { rovers: [], activeRover: null };

  const roverSelect = document.getElementById("rover-select");
  const sshBtn = document.getElementById("ssh-btn");
  const addIpBtn = document.getElementById("add-ip-btn");
  const rescanBtn = document.getElementById("rescan-btn");
  const refreshHealthBtn = document.getElementById("refresh-health-btn");
  const executeBtn = document.getElementById("execute-btn");
  const codeInput = document.getElementById("code-input");
  const timeoutInput = document.getElementById("timeout-seconds");

  const modalOverlay = document.getElementById("modal-overlay");
  const modalTitle = document.getElementById("modal-title");
  const modalBody = document.getElementById("modal-body");
  const modalClose = document.getElementById("modal-close");

  const cameraRoverName = document.getElementById("camera-rover-name");
  const cameraFeed = document.getElementById("camera-feed");
  const healthStatus = document.getElementById("health-status");
  const healthTemp = document.getElementById("health-temp");
  const healthCpu = document.getElementById("health-cpu");
  const healthMemory = document.getElementById("health-memory");
  const healthDisk = document.getElementById("health-disk");
  const healthMessage = document.getElementById("health-message");
  const executeMessage = document.getElementById("execute-message");
  const executionOutput = document.getElementById("execution-output");

  const state = {
    rovers: Array.isArray(initial.rovers) ? [...initial.rovers] : [],
  };

  let activeRover = initial.activeRover;

  function setMessage(element, text, kind) {
    element.textContent = text || "";
    element.classList.remove("ok", "error");
    if (kind) {
      element.classList.add(kind);
    }
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

  function showAddIpModal(contextMessage) {
    const message = contextMessage
      ? `<p class="subtext">${escapeHtml(contextMessage)}</p>`
      : "<p class=\"subtext\">Enter a rover IP to create a direct low-latency control target.</p>";

    showModal(
      "Add Rover by IP",
      `
        ${message}
        <div class="modal-form-row">
          <label for="modal-ip-input">Rover IP address</label>
          <input id="modal-ip-input" type="text" placeholder="e.g. 192.168.1.42" autocomplete="off" />
        </div>
        <div class="modal-actions">
          <button id="modal-add-ip-submit" class="btn" type="button">Add and Switch</button>
        </div>
        <p class="subtext">Tip: IP targets usually reduce name-resolution latency compared to hostname aliases.</p>
      `
    );
  }

  function setCameraPlaceholder() {
    if (!activeRover) {
      cameraRoverName.textContent = "No rover selected.";
      if (cameraFeed) {
        cameraFeed.classList.add("hidden");
        cameraFeed.removeAttribute("src");
      }
      if (window.RoverMapping && typeof window.RoverMapping.setActiveRover === "function") {
        window.RoverMapping.setActiveRover(null);
      }
      return;
    }

    cameraRoverName.textContent = `Live stream from ${activeRover.name} (${activeRover.host})`;
    if (cameraFeed) {
      cameraFeed.src = activeRover.camera_mjpeg_url;
      cameraFeed.classList.remove("hidden");
    }
    if (window.RoverMapping && typeof window.RoverMapping.setActiveRover === "function") {
      window.RoverMapping.setActiveRover(activeRover);
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

  function renderHealthSummary(summary) {
    healthStatus.textContent = (summary.status || "unknown").toString().toUpperCase();
    healthTemp.textContent =
      summary.max_temp_c !== null && summary.max_temp_c !== undefined
        ? `${summary.max_temp_c} °C`
        : "--";
    healthCpu.textContent = formatCpu(summary.cpu_load);
    healthMemory.textContent = formatMemory(summary.memory);
    healthDisk.textContent = formatDisk(summary.disk);
  }

  function renderExecutionResult(result) {
    executionOutput.textContent = JSON.stringify(result, null, 2);
  }

  async function refreshHealth() {
    setMessage(healthMessage, "Fetching rover health…");

    try {
      const response = await fetch("/api/health");
      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Health request failed (${response.status})`);
      }

      renderHealthSummary(payload.summary);
      setMessage(healthMessage, `Health updated for ${payload.rover.name}.`, "ok");
    } catch (error) {
      setMessage(healthMessage, `Health unavailable: ${error.message}`, "error");
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
      setCameraPlaceholder();
      setMessage(healthMessage, `Active rover: ${activeRover.name}`, "ok");
      await refreshHealth();
    } catch (error) {
      setMessage(healthMessage, `Unable to switch rover: ${error.message}`, "error");
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
  }

  async function addRoverByIp(ipAddress) {
    const response = await fetch("/api/add-rover-ip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip_address: ipAddress }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Unable to add IP (${response.status})`);
    }

    state.rovers = Array.isArray(payload.rovers) ? payload.rovers : state.rovers;
    activeRover = payload.active_rover || activeRover;
    populateRoverOptions();
    setCameraPlaceholder();
    window.RoverMapping?.setRovers(state.rovers);

    if (activeRover) {
      roverSelect.value = activeRover.name;
    }

    setMessage(healthMessage, payload.message || "Rover IP added.", "ok");
    await refreshHealth();
  }

  async function scanRovers(triggeredByRescan) {
    const response = await fetch("/api/scan-rovers");
    const payload = await response.json();

    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Scan failed (${response.status})`);
    }

    const discovered = payload.discovered || [];
    const newlyDiscovered = payload.newly_discovered || [];

    if (!triggeredByRescan && payload.show_ip_input) {
      showAddIpModal(
        `Detected online devices: ${discovered.join(", ")}. Enter a direct IP now for faster and lower-latency control.`
      );
      return;
    }

    if (triggeredByRescan && newlyDiscovered.length > 0) {
      showAddIpModal(
        `New device(s) found: ${newlyDiscovered.join(", ")}. Add an IP target to switch quickly.`
      );
      return;
    }

    if (triggeredByRescan) {
      const label = discovered.length ? discovered.join(", ") : "none";
      setMessage(healthMessage, `Rescan complete. Online: ${label}.`, "ok");
    }
  }

  async function executeCode() {
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
      setMessage(executeMessage, `Execution error: ${error.message}`, "error");
      executionOutput.textContent = "No execution result received due to request error.";
    } finally {
      executeBtn.disabled = false;
    }
  }

  async function showSshHelp() {
    setMessage(executeMessage, "Loading SSH instructions…");

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

      setMessage(executeMessage, `SSH steps ready for ${payload.rover?.name || "rover"}.`, "ok");
    } catch (error) {
      setMessage(executeMessage, `SSH help error: ${error.message}`, "error");
    }
  }

  function populateRoverOptions() {
    roverSelect.innerHTML = "";

    for (const rover of state.rovers || []) {
      const option = document.createElement("option");
      option.value = rover.name;
      option.textContent = `${rover.name} (${rover.host})`;
      if (activeRover && rover.name === activeRover.name) {
        option.selected = true;
      }
      roverSelect.appendChild(option);
    }
  }

  function wireEvents() {
    roverSelect.addEventListener("change", async (event) => {
      await switchRover(event.target.value);
    });

    addIpBtn.addEventListener("click", () => showAddIpModal());
    rescanBtn.addEventListener("click", async () => {
      try {
        await scanRovers(true);
      } catch (error) {
        setMessage(healthMessage, `Rescan failed: ${error.message}`, "error");
      }
    });
    refreshHealthBtn.addEventListener("click", refreshHealth);
    executeBtn.addEventListener("click", executeCode);
    sshBtn.addEventListener("click", showSshHelp);

    modalClose.addEventListener("click", closeModal);
    modalOverlay.addEventListener("click", (event) => {
      if (event.target === modalOverlay) {
        closeModal();
      }
    });

    modalBody.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }

      if (target.id !== "modal-add-ip-submit") {
        return;
      }

      const ipInput = document.getElementById("modal-ip-input");
      const ipValue = ipInput instanceof HTMLInputElement ? ipInput.value.trim() : "";

      if (!ipValue) {
        setMessage(healthMessage, "Enter an IP address before adding.", "error");
        return;
      }

      try {
        await addRoverByIp(ipValue);
        closeModal();
      } catch (error) {
        setMessage(healthMessage, `Unable to add IP: ${error.message}`, "error");
      }
    });
  }

  async function start() {
    try {
      await refreshRoversFromServer();
    } catch (error) {
      setMessage(healthMessage, `Could not refresh rover list: ${error.message}`, "error");
    }

    populateRoverOptions();
    setCameraPlaceholder();
    window.RoverMapping?.setRovers(state.rovers);
    wireEvents();
    await refreshHealth();

    try {
      await scanRovers(false);
    } catch (error) {
      setMessage(healthMessage, `Initial rover scan failed: ${error.message}`, "error");
    }

    setInterval(refreshHealth, 10000);
  }

  start();
})();

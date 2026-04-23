from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, session

from services.rover_client import (
    RoverClientError,
    RoverTarget,
    execute_code,
    fetch_health,
    load_rover_targets,
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "rover-ui-local-dev-key"

    config_path = Path(__file__).parent / "config" / "rovers.yaml"
    rover_targets = load_rover_targets(config_path)

    if not rover_targets:
        raise RuntimeError(
            "No rovers configured. Update UI/config/rovers.yaml with at least one rover."
        )

    rover_lookup = {rover.name: rover for rover in rover_targets}

    def _all_rovers() -> list[dict[str, Any]]:
        return [rover.to_dict() for rover in rover_targets]

    def _build_ssh_steps(rover: RoverTarget) -> list[str]:
        command = f"ssh {rover.ssh_username}@{rover.host}"
        return [
            "Open Command Prompt (Win + R, type 'cmd', then press Enter).",
            f"Type this exact command and press Enter: {command}",
            "When prompted to trust the host key, type 'yes' and press Enter.",
            "Enter the rover password when prompted (characters may be hidden while typing).",
            "After login, you are connected to the rover shell and can run rover commands.",
            "To disconnect safely, type 'exit' and press Enter.",
        ]

    def _scan_known_rovers() -> list[str]:
        candidates = []
        for rover_name in ("rover0", "rover1"):
            candidates.append(rover_lookup.get(rover_name) or RoverTarget(name=rover_name, host=rover_name))

        discovered: list[str] = []
        for rover in candidates:
            try:
                fetch_health(rover, timeout=1.5)
                discovered.append(rover.name)
            except RoverClientError:
                continue

        return discovered

    def _active_rover() -> RoverTarget:
        active_name = session.get("active_rover")
        if active_name in rover_lookup:
            return rover_lookup[active_name]

        default_rover = rover_targets[0]
        session["active_rover"] = default_rover.name
        return default_rover

    @app.get("/")
    def index() -> Any:
        active = _active_rover()
        return render_template(
            "index.html",
            rovers=_all_rovers(),
            active_rover=active.to_dict(),
        )

    @app.get("/api/rovers")
    def get_rovers() -> Any:
        return jsonify({"ok": True, "rovers": _all_rovers(), "active_rover": _active_rover().to_dict()})

    @app.get("/api/active-rover")
    def get_active_rover() -> Any:
        return jsonify({"active_rover": _active_rover().to_dict()})

    @app.post("/api/select-rover")
    def select_rover() -> Any:
        payload = request.get_json(silent=True) or {}
        rover_name = str(payload.get("rover_name", "")).strip()

        if rover_name not in rover_lookup:
            return jsonify({"ok": False, "error": f"Unknown rover '{rover_name}'"}), 400

        session["active_rover"] = rover_name
        return jsonify({"ok": True, "active_rover": rover_lookup[rover_name].to_dict()})

    @app.post("/api/add-rover-ip")
    def add_rover_ip() -> Any:
        payload = request.get_json(silent=True) or {}
        ip_address = str(payload.get("ip_address", "")).strip()

        if not ip_address:
            return jsonify({"ok": False, "error": "IP address is required."}), 400

        try:
            parsed_ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid IP address."}), 400

        host = str(parsed_ip)
        existing = next((rover for rover in rover_targets if rover.host == host), None)
        if existing:
            session["active_rover"] = existing.name
            return jsonify(
                {
                    "ok": True,
                    "message": f"Using existing rover target {existing.name}.",
                    "active_rover": existing.to_dict(),
                    "rovers": _all_rovers(),
                }
            )

        base_name = f"ip-{host.replace('.', '-') }"
        candidate_name = base_name
        suffix = 2
        while candidate_name in rover_lookup:
            candidate_name = f"{base_name}-{suffix}"
            suffix += 1

        new_rover = RoverTarget(name=candidate_name, host=host)
        rover_targets.append(new_rover)
        rover_lookup[new_rover.name] = new_rover
        session["active_rover"] = new_rover.name

        return jsonify(
            {
                "ok": True,
                "message": f"Added rover target {new_rover.name} ({new_rover.host}).",
                "active_rover": new_rover.to_dict(),
                "rovers": _all_rovers(),
            }
        )

    @app.get("/api/scan-rovers")
    def scan_rovers() -> Any:
        discovered = _scan_known_rovers()
        previous = set(session.get("discovered_rovers", []))
        current = set(discovered)
        newly_discovered = sorted(current - previous)
        session["discovered_rovers"] = sorted(current)

        return jsonify(
            {
                "ok": True,
                "discovered": sorted(current),
                "newly_discovered": newly_discovered,
                "show_ip_input": bool(current),
            }
        )

    @app.get("/api/health")
    def get_health() -> Any:
        rover = _active_rover()
        try:
            result = fetch_health(rover)
            return jsonify(
                {
                    "ok": True,
                    "rover": rover.to_dict(),
                    "summary": result["summary"],
                    "raw": result["raw"],
                }
            )
        except RoverClientError as exc:
            return jsonify({"ok": False, "rover": rover.to_dict(), "error": str(exc)}), 502

    @app.post("/api/execute")
    def execute() -> Any:
        rover = _active_rover()
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code", ""))
        timeout_seconds = float(payload.get("timeout_seconds", 60.0))

        if not code.strip():
            return jsonify({"ok": False, "error": "Code cannot be empty."}), 400

        try:
            result = execute_code(rover, code=code, timeout_seconds=timeout_seconds)
            return jsonify({"ok": True, "rover": rover.to_dict(), "result": result})
        except RoverClientError as exc:
            return jsonify({"ok": False, "rover": rover.to_dict(), "error": str(exc)}), 502

    @app.get("/api/ssh-instructions")
    def ssh_instructions() -> Any:
        rover = _active_rover()
        return jsonify(
            {
                "ok": True,
                "rover": rover.to_dict(),
                "command": f"ssh {rover.ssh_username}@{rover.host}",
                "steps": _build_ssh_steps(rover),
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
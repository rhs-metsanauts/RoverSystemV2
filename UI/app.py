from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, session, Response, stream_with_context

from services.rover_client import (
    RoverClientError,
    RoverTarget,
    execute_code,
    fetch_health,
    load_rover_targets,
)

model = 'qwen3-coder-next:cloud'

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

    def _ip_overrides() -> dict[str, str]:
        stored = session.get("ip_overrides")
        if not isinstance(stored, dict):
            return {}

        return {str(key): str(value) for key, value in stored.items() if str(value).strip()}

    def _set_ip_override(rover_name: str, ip_address: str | None) -> None:
        overrides = _ip_overrides()

        if ip_address and ip_address.strip():
            overrides[rover_name] = ip_address.strip()
        else:
            overrides.pop(rover_name, None)

        session["ip_overrides"] = overrides

    def _resolved_rover(rover: RoverTarget) -> RoverTarget:
        override = _ip_overrides().get(rover.name)
        if override:
            return replace(rover, host=override)
        return rover

    def _serialize_rover(rover: RoverTarget) -> dict[str, Any]:
        resolved = _resolved_rover(rover)
        data = resolved.to_dict()
        data["canonical_host"] = rover.host
        data["ip_override"] = _ip_overrides().get(rover.name)
        return data

    def _visible_rover_targets() -> list[RoverTarget]:
        discovered = session.get("discovered_rovers")
        if not isinstance(discovered, list):
            return rover_targets

        discovered_set = {str(name) for name in discovered}
        if not discovered_set:
            return []

        visible = [rover for rover in rover_targets if rover.name in discovered_set]
        return visible

    def _all_rovers() -> list[dict[str, Any]]:
        return [_serialize_rover(rover) for rover in _visible_rover_targets()]

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
        candidates = rover_targets

        discovered: list[str] = []
        for rover in candidates:
            try:
                fetch_health(_resolved_rover(rover), timeout=1.5)
                discovered.append(rover.name)
            except RoverClientError:
                continue

        return discovered

    def _active_rover() -> RoverTarget:
        active_name = session.get("active_rover")
        visible_rovers = _visible_rover_targets()
        visible_lookup = {rover.name: rover for rover in visible_rovers}

        if active_name in visible_lookup:
            return visible_lookup[active_name]

        if visible_rovers:
            default_rover = visible_rovers[0]
            session["active_rover"] = default_rover.name
            return default_rover

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
            active_rover=_serialize_rover(active),
        )

    @app.get("/api/rovers")
    def get_rovers() -> Any:
        active = _active_rover()
        return jsonify({"ok": True, "rovers": _all_rovers(), "active_rover": _serialize_rover(active)})

    @app.get("/api/active-rover")
    def get_active_rover() -> Any:
        return jsonify({"active_rover": _serialize_rover(_active_rover())})

    @app.post("/api/select-rover")
    def select_rover() -> Any:
        payload = request.get_json(silent=True) or {}
        rover_name = str(payload.get("rover_name", "")).strip()

        visible_lookup = {rover.name: rover for rover in _visible_rover_targets()}

        if rover_name not in visible_lookup:
            return jsonify({"ok": False, "error": f"Unknown rover '{rover_name}'"}), 400

        session["active_rover"] = rover_name
        return jsonify({"ok": True, "active_rover": _serialize_rover(visible_lookup[rover_name])})

    @app.post("/api/add-rover-ip")
    def add_rover_ip() -> Any:
        payload = request.get_json(silent=True) or {}
        rover_name = str(payload.get("rover_name", "")).strip()
        ip_address = str(payload.get("ip_address", "")).strip()

        if rover_name not in rover_lookup:
            return jsonify({"ok": False, "error": f"Unknown rover '{rover_name}'"}), 400

        if not ip_address:
            _set_ip_override(rover_name, None)
            session["active_rover"] = rover_name
            return jsonify(
                {
                    "ok": True,
                    "message": f"Cleared IP alias override for {rover_name}; using hostname alias.",
                    "active_rover": _serialize_rover(rover_lookup[rover_name]),
                    "rovers": _all_rovers(),
                }
            )

        try:
            parsed_ip = ipaddress.ip_address(ip_address)
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid IP address."}), 400

        host = str(parsed_ip)
        _set_ip_override(rover_name, host)
        session["active_rover"] = rover_name

        return jsonify(
            {
                "ok": True,
                "message": f"Set IP alias override for {rover_name} -> {host}.",
                "active_rover": _serialize_rover(rover_lookup[rover_name]),
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
        active = _active_rover()

        return jsonify(
            {
                "ok": True,
                "rovers": _all_rovers(),
                "active_rover": _serialize_rover(active),
                "discovered": sorted(current),
                "newly_discovered": newly_discovered,
            }
        )

    @app.get("/api/health")
    def get_health() -> Any:
        rover = _active_rover()
        resolved = _resolved_rover(rover)
        try:
            result = fetch_health(resolved)
            return jsonify(
                {
                    "ok": True,
                    "rover": _serialize_rover(rover),
                    "summary": result["summary"],
                    "raw": result["raw"],
                }
            )
        except RoverClientError as exc:
            return jsonify({"ok": False, "rover": _serialize_rover(rover), "error": str(exc)}), 502

    @app.get("/api/health-all")
    def get_health_all() -> Any:
        results: list[dict[str, Any]] = []

        for rover in rover_targets:
            resolved = _resolved_rover(rover)
            try:
                health = fetch_health(resolved)
                results.append(
                    {
                        "ok": True,
                        "rover": _serialize_rover(rover),
                        "summary": health["summary"],
                        "raw": health["raw"],
                    }
                )
            except RoverClientError as exc:
                results.append(
                    {
                        "ok": False,
                        "rover": _serialize_rover(rover),
                        "error": str(exc),
                    }
                )

        return jsonify({"ok": True, "results": results})

    @app.post("/api/execute")
    def execute() -> Any:
        rover = _active_rover()
        resolved = _resolved_rover(rover)
        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code", ""))
        timeout_seconds = float(payload.get("timeout_seconds", 60.0))

        if not code.strip():
            return jsonify({"ok": False, "error": "Code cannot be empty."}), 400

        try:
            result = execute_code(resolved, code=code, timeout_seconds=timeout_seconds)
            return jsonify({"ok": True, "rover": _serialize_rover(rover), "result": result})
        except RoverClientError as exc:
            return jsonify({"ok": False, "rover": _serialize_rover(rover), "error": str(exc)}), 502

    @app.get("/api/ssh-instructions")
    def ssh_instructions() -> Any:
        rover = _active_rover()
        resolved = _resolved_rover(rover)
        return jsonify(
            {
                "ok": True,
                "rover": _serialize_rover(rover),
                "command": f"ssh {resolved.ssh_username}@{resolved.host}",
                "steps": _build_ssh_steps(resolved),
            }
        )

    def _extract_code_from_markdown(text: str) -> str:
        """Extract Python code from markdown code blocks, or return raw text if no blocks found."""
        import re
        # Match ```python ... ``` or ``` ... ``` blocks
        pattern = r'```(?:python)?\s*\n(.*?)\n```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            # Return all matched code blocks joined by newlines
            return '\n'.join(matches)
        # If no markdown blocks, return the original text
        return text

    @app.post("/api/ai_command")
    def ai_command() -> Any:
        """Stream an LLM response that generates Python code for rover control."""
        try:
            from ollama import chat
        except ImportError:
            return jsonify({"ok": False, "error": "ollama library not installed. Install with: pip install ollama"}), 500

        try:
            payload = request.get_json(silent=True) or {}
            user_message = str(payload.get("message", "")).strip()
            history = payload.get("history", [])

            if not user_message:
                return jsonify({"ok": False, "error": "No message provided"}), 400

            # Load AI system prompt from markdown file
            prompt_path = Path(__file__).parent.parent / "ai_system_prompt.md"
            if not prompt_path.exists():
                return jsonify({"ok": False, "error": "AI system prompt not found"}), 500

            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()

            # Build messages list: system prompt, then history, then user message
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            def generate():
                """SSE generator that streams the LLM response."""
                content_buffer = ""
                try:
                    stream = chat(
                        model="qwen3-coder-next:cloud",
                        messages=messages,
                        stream=True,
                        options={'num_predict': 2000},
                        keep_alive='1h',
                    )

                    for chunk in stream:
                        if chunk.message.content:
                            content = chunk.message.content
                            content_buffer += content
                            yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"

                    # Extract code from markdown blocks
                    extracted_code = _extract_code_from_markdown(content_buffer)
                    yield f"data: {json.dumps({'type': 'result', 'code': extracted_code})}\n\n"

                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

                yield "data: {\"type\": \"done\"}\n\n"

            return Response(stream_with_context(generate()), mimetype='text/event-stream')

        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
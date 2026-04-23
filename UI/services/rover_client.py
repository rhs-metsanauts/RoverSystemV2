from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml


class RoverClientError(RuntimeError):
    """Raised when rover communication fails."""


@dataclass(frozen=True)
class RoverTarget:
    name: str
    host: str
    control_port: int = 8002
    camera_port: int = 8001
    ssh_username: str = "rover"

    @property
    def control_base_url(self) -> str:
        return f"http://{self.host}:{self.control_port}"

    @property
    def camera_mjpeg_url(self) -> str:
        return f"http://{self.host}:{self.camera_port}/video.mjpg"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "control_port": self.control_port,
            "camera_port": self.camera_port,
            "ssh_username": self.ssh_username,
            "control_base_url": self.control_base_url,
            "camera_mjpeg_url": self.camera_mjpeg_url,
        }


def load_rover_targets(config_path: Path) -> list[RoverTarget]:
    if not config_path.exists():
        raise RuntimeError(f"Rover config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    rovers = data.get("rovers", [])
    if not isinstance(rovers, list):
        raise RuntimeError("Invalid rover config: 'rovers' must be a list")

    targets: list[RoverTarget] = []
    for rover in rovers:
        name = str(rover.get("name", "")).strip()
        host = str(rover.get("host", "")).strip()
        if not name or not host:
            continue

        targets.append(
            RoverTarget(
                name=name,
                host=host,
                control_port=int(rover.get("control_port", 8002)),
                camera_port=int(rover.get("camera_port", 8001)),
                ssh_username=str(rover.get("ssh_username", "rover")),
            )
        )

    return targets


def _choose_root_disk(disk_usage: Any) -> dict[str, Any] | None:
    if not isinstance(disk_usage, list):
        return None

    root_entry = next((entry for entry in disk_usage if entry.get("mount") == "/"), None)
    if root_entry:
        return root_entry

    return disk_usage[0] if disk_usage else None


def summarize_health(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("report") if isinstance(payload, dict) else {}
    report = report if isinstance(report, dict) else {}

    cpu = report.get("cpu_load") if isinstance(report.get("cpu_load"), dict) else {}
    memory = report.get("memory") if isinstance(report.get("memory"), dict) else {}
    disk = _choose_root_disk(report.get("disk_usage"))

    return {
        "status": payload.get("status", "unknown"),
        "max_temp_c": report.get("max_temp_c"),
        "cpu_load": {
            "1min": cpu.get("1min"),
            "5min": cpu.get("5min"),
            "15min": cpu.get("15min"),
        },
        "memory": {
            "total": memory.get("total"),
            "used": memory.get("used"),
            "free": memory.get("free"),
            "available": memory.get("available"),
        },
        "disk": {
            "mount": disk.get("mount") if disk else None,
            "size": disk.get("size") if disk else None,
            "used": disk.get("used") if disk else None,
            "available": disk.get("available") if disk else None,
            "use_pct": disk.get("use_pct") if disk else None,
        },
    }


def fetch_health(rover: RoverTarget, timeout: float = 5.0) -> dict[str, Any]:
    endpoint = f"{rover.control_base_url}/health"

    try:
        response = requests.get(endpoint, timeout=timeout)
    except requests.RequestException as exc:
        raise RoverClientError(f"Health check failed for {rover.name}: {exc}") from exc

    if not response.ok:
        raise RoverClientError(
            f"Health check failed for {rover.name}: HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RoverClientError(
            f"Health check failed for {rover.name}: invalid JSON response"
        ) from exc

    return {"raw": payload, "summary": summarize_health(payload)}


def execute_code(rover: RoverTarget, code: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
    endpoint = f"{rover.control_base_url}/execute"
    body = {
        "code": code,
        "timeout_seconds": timeout_seconds,
    }

    request_timeout = max(timeout_seconds + 3.0, 10.0)
    try:
        response = requests.post(endpoint, json=body, timeout=request_timeout)
    except requests.RequestException as exc:
        raise RoverClientError(f"Execution request failed for {rover.name}: {exc}") from exc

    if not response.ok:
        raise RoverClientError(
            f"Execution request failed for {rover.name}: HTTP {response.status_code}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RoverClientError(
            f"Execution request failed for {rover.name}: invalid JSON response"
        ) from exc
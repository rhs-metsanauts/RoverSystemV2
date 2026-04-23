"""
rover_health.py
Diagnostic functions for checking the health of a Jetson Orin Nano-based rover.
"""
from __future__ import annotations

import subprocess
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: str) -> tuple[str, str, int]:
    """Run a shell command and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _read_file(path: str) -> str | None:
    """Read a sysfs/procfs file, returning None on failure."""
    try:
        data = open(path, "rb").read()
        if data is None:
            return None
        return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Thermals
# ---------------------------------------------------------------------------

def get_temperatures() -> dict[str, float]:
    """
    Return all thermal zone temperatures in °C.
    Keys are zone names (e.g. 'thermal_zone0') or the type string if available.
    """
    temps = {}
    thermal_base = Path("/sys/class/thermal")
    for zone in sorted(thermal_base.glob("thermal_zone*")):
        try:
            raw = _read_file(str(zone / "temp"))
            if not raw:
                continue
            zone_type = _read_file(str(zone / "type")) or zone.name
            temps[zone_type] = round(int(raw) / 1000.0, 1)
        except Exception:
            continue
    return temps


def get_max_temperature() -> float | None:
    """Return the highest temperature reading across all thermal zones (°C)."""
    temps = get_temperatures()
    return max(temps.values()) if temps else None


# ---------------------------------------------------------------------------
# Power & Performance (Jetson-specific)
# ---------------------------------------------------------------------------

def get_tegrastats_snapshot(interval_ms: int = 500) -> str:
    """
    Capture a single tegrastats line.
    Returns the raw output string, or an error message if unavailable.
    """
    stdout, stderr, rc = _run(f"tegrastats --interval {interval_ms} --count 1")
    if rc != 0:
        return f"Error: {stderr or 'tegrastats not available'}"
    return stdout


def get_power_mode() -> str:
    """Return the current nvpmodel power mode string."""
    stdout, stderr, rc = _run("nvpmodel -q")
    if rc != 0:
        return f"Error: {stderr or 'nvpmodel not available'}"
    return stdout


def get_clock_frequencies() -> dict[str, str]:
    """
    Return current clock frequencies from jetson_clocks.
    Returns a dict of {component: frequency_string}.
    """
    stdout, stderr, rc = _run("jetson_clocks --show")
    if rc != 0:
        return {"error": stderr or "jetson_clocks not available"}

    clocks = {}
    for line in stdout.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            clocks[key.strip()] = val.strip()
    return clocks


# ---------------------------------------------------------------------------
# Compute Load
# ---------------------------------------------------------------------------

def get_memory_usage() -> dict[str, str]:
    """
    Return memory statistics (total, used, free, available) as human-readable strings.
    """
    stdout, _, rc = _run("free -h")
    if rc != 0:
        return {"error": "free command failed"}

    lines = stdout.splitlines()
    if len(lines) < 2:
        return {"error": "unexpected output from free"}

    headers = lines[0].split()
    values = lines[1].split()
    # values[0] is "Mem:", rest align with headers
    result = {}
    for i, header in enumerate(headers):
        try:
            result[header] = values[i + 1]
        except IndexError:
            pass
    return result


def get_top_processes(n: int = 10) -> list[dict]:
    """
    Return the top N processes by CPU usage.
    Each entry: {pid, user, cpu_pct, mem_pct, command}
    """
    stdout, _, rc = _run(
        f"ps aux --sort=-%cpu | head -n {n + 1}"
    )
    if rc != 0:
        return []

    processes = []
    lines = stdout.splitlines()[1:]  # skip header
    for line in lines:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        processes.append({
            "pid": parts[1],
            "user": parts[0],
            "cpu_pct": parts[2],
            "mem_pct": parts[3],
            "command": parts[10],
        })
    return processes


def get_cpu_load() -> dict[str, float]:
    """Return 1-min, 5-min, and 15-min load averages."""
    raw = _read_file("/proc/loadavg")
    if raw is None:
        return {"error": -1.0}
    parts = raw.split()
    return {
        "1min": float(parts[0]),
        "5min": float(parts[1]),
        "15min": float(parts[2]),
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def get_disk_usage() -> list[dict]:
    """
    Return disk usage for all mounted filesystems.
    Each entry: {filesystem, size, used, available, use_pct, mount}
    """
    stdout, _, rc = _run("df -h")
    if rc != 0:
        return []

    entries = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6:
            entries.append({
                "filesystem": parts[0],
                "size": parts[1],
                "used": parts[2],
                "available": parts[3],
                "use_pct": parts[4],
                "mount": parts[5],
            })
    return entries


def get_disk_io() -> str:
    """Return a snapshot of disk I/O statistics via iostat."""
    stdout, stderr, rc = _run("iostat -d 1 1")
    if rc != 0:
        return f"Error: {stderr or 'iostat not available'}"
    return stdout


# ---------------------------------------------------------------------------
# Networking / Comms
# ---------------------------------------------------------------------------

def get_network_interfaces() -> dict[str, dict]:
    """
    Return network interface info (IP addresses, state).
    """
    stdout, _, rc = _run("ip -o addr show")
    if rc != 0:
        return {}

    interfaces: dict[str, dict] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        family = parts[2]
        addr = parts[3]
        if iface not in interfaces:
            interfaces[iface] = {"addresses": []}
        interfaces[iface]["addresses"].append({"family": family, "address": addr})

    # Get link state
    link_out, _, _ = _run("ip -o link show")
    for line in link_out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        iface = parts[1].rstrip(":")
        state = "UP" if "UP" in line else "DOWN"
        if iface in interfaces:
            interfaces[iface]["state"] = state

    return interfaces


def ping_host(host: str = "8.8.8.8", count: int = 3) -> dict:
    """
    Ping a host and return summary stats.
    Returns {host, packets_sent, packets_received, packet_loss, avg_rtt_ms}
    """
    stdout, stderr, rc = _run(f"ping -c {count} -W 2 {host}")
    result: dict = {"host": host, "reachable": rc == 0}

    loss_match = re.search(r"(\d+)% packet loss", stdout)
    rtt_match = re.search(r"rtt .* = [\d.]+/([\d.]+)/", stdout)

    result["packet_loss_pct"] = int(loss_match.group(1)) if loss_match else None
    result["avg_rtt_ms"] = float(rtt_match.group(1)) if rtt_match else None
    return result


def get_open_ports() -> list[dict]:
    """
    Return listening TCP/UDP ports.
    Each entry: {protocol, local_address, port, state}
    """
    stdout, _, rc = _run("ss -tuln")
    if rc != 0:
        return []

    ports = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[4]
        addr, _, port = local.rpartition(":")
        ports.append({
            "protocol": parts[0],
            "local_address": addr,
            "port": port,
            "state": parts[1],
        })
    return ports


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def get_dmesg_tail(lines: int = 50) -> list[str]:
    """Return the last N lines of the kernel ring buffer."""
    stdout, _, rc = _run(f"dmesg | tail -n {lines}")
    if rc != 0:
        return []
    return stdout.splitlines()


def get_journal_errors(lines: int = 50) -> list[str]:
    """Return recent error/warning entries from the systemd journal."""
    stdout, _, rc = _run(
        f"journalctl -p err -n {lines} --no-pager --output=short"
    )
    if rc != 0:
        return []
    return stdout.splitlines()


# ---------------------------------------------------------------------------
# Full Health Report
# ---------------------------------------------------------------------------

def get_full_health_report() -> dict:
    """
    Run all health checks and return a single consolidated dict.
    Suitable for logging or sending over telemetry.
    """
    return {
        "temperatures_c": get_temperatures(),
        "max_temp_c": get_max_temperature(),
        "memory": get_memory_usage(),
        "cpu_load": get_cpu_load(),
        "disk_usage": get_disk_usage(),
        "network_interfaces": get_network_interfaces(),
        "power_mode": get_power_mode(),
        "top_processes": get_top_processes(5),
    }


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== Rover Health Report ===\n")
    report = get_full_health_report()
    print(json.dumps(report, indent=2))
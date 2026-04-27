from __future__ import annotations

import os
import subprocess
from typing import Any


def launch_ssh_terminal(host: str, username: str = "rover") -> dict[str, Any]:
    if os.name != "nt":
        return {
            "ok": False,
            "error": "SSH terminal launch is currently implemented for Windows only.",
        }

    ssh_command = f"ssh {username}@{host}"

    try:
        subprocess.Popen(["cmd.exe", "/K", ssh_command])
        return {
            "ok": True,
            "message": "Opened Command Prompt with SSH command.",
            "command": ssh_command,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"Unable to launch terminal: {exc}",
            "command": ssh_command,
        }

if __name__ == "__main__":
    result = launch_ssh_terminal("rover0")
    print(result)
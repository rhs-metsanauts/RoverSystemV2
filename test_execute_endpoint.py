#!/usr/bin/env python3
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    api_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002/execute"

    code = '''from Rover import Rover
import time

rover = Rover()
time.sleep(2)
rover.forwardForDuration(1, 2)
time.sleep(1)
rover.turnLeftForDuration(1, 1)
time.sleep(1)
rover.turnRightForDuration(1, 1)
rover.toSunPosition()
rover.stop()
'''

    payload = {
        "code": code,
        "timeout_seconds": 60.0,
    }

    print(f"Posting test program to {api_url}")

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(body)
            return 0
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(error_body)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
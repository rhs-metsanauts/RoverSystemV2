import ast
import io
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import Health as RoverHealth
from Rover import Rover


app = FastAPI(title="Rover Programmatic Control Server")

rover: Rover | None = None
execution_context: Dict[str, Any] = {}
_execute_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


class ExecuteRequest(BaseModel):
    code: str = Field(..., description="Python source code to execute")
    timeout_seconds: float = Field(
        default=60.0,
        description="Maximum execution time in seconds",
        gt=0,
        le=600,
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        safe_dict: Dict[str, Any] = {}
        for k, v in value.items():
            safe_dict[str(k)] = _json_safe(v)
        return safe_dict
    return repr(value)


def _execute_python(code: str, timeout_seconds: float = 60.0) -> Dict[str, Any]:
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="code must be a non-empty string")

    def _run_code() -> Dict[str, Any]:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            parsed = ast.parse(code, mode="exec")
            compiled = compile(parsed, "<api-exec>", "exec")
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(compiled, execution_context, execution_context)
            return {
                "ok": True,
                "stdout": stdout_buffer.getvalue(),
                "stderr": stderr_buffer.getvalue(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "stdout": stdout_buffer.getvalue(),
                "stderr": stderr_buffer.getvalue(),
            }

    with _execute_lock:
        try:
            future = _executor.submit(_run_code)
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            return {
                "ok": False,
                "error": f"Execution timed out after {timeout_seconds} seconds",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Execution failed: {str(exc)}",
            }


@app.on_event("startup")
def startup_event():
    global rover, execution_context
    rover = Rover()
    execution_context = {
        "__builtins__": __builtins__,
        "rover": rover,
        "Rover": Rover,
        "health": RoverHealth,
    }


@app.get("/")
def index() -> Dict[str, str]:
    return {
        "service": "rover-fastapi",
        "execute": "POST /execute",
        "health": "GET /health",
    }


@app.post("/execute")
def execute_python(request: ExecuteRequest) -> Dict[str, Any]:
    return _execute_python(request.code, timeout_seconds=request.timeout_seconds)


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        return {
            "status": "ok",
            "rover_initialized": rover is not None,
            "report": RoverHealth.get_full_health_report(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"health check failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("RoverFastApiServer:app", host="0.0.0.0", port=8002, reload=False)
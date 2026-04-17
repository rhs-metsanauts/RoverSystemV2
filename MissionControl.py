from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import requests
import json
import os
from ollama import chat
from pydantic import BaseModel
from typing import Optional
import threading
import queue
import time
import websocket as ws_client   # websocket-client package

app = Flask(__name__)

# Configuration
FASTAPI_SERVER_URL = "http://192.168.137.104:8000/execute"
DEFAULT_TIMEOUT = 35
TRANSMISSION_MODE = "wifi"  # "wifi" or "lora"
LORA_DESTINATION = 0  # Integer destination for LoRA
LORA_MESSAGE_PATH = r"D:\message.json"  # Path where LoRA transmitter reads messages from
OLLAMA_MODEL = "qwen3:0.6b"  # Ollama model for AI assistant

# ── 3D Mapping globals ────────────────────────────────────────────────────────
JETSON_WS_URL = "ws://192.168.1.100:9001"   # Set via /config in the UI
_sse_clients: list[queue.Queue] = []         # One queue per open SSE connection
_sse_lock = threading.Lock()
_jetson_ws_lock = threading.Lock()
_jetson_ws_connected = False
_jetson_ws_handle = None                     # websocket.WebSocketApp instance
_map_point_count = 0
_map_seq = -1

# Load AI system prompt from markdown file
_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_system_prompt.md")
with open(_PROMPT_PATH, "r", encoding="utf-8") as _f:
    AI_SYSTEM_PROMPT = _f.read()

# Load Robot.py and extract just the essential API documentation (not full source)
_ROBOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Robot.py")
_ROBOT_API_DOCS = """
## Rover Class API Reference

```python
rover = Rover()  # Initialize the rover

# Movement Methods
rover.forward(power, duration=None)     # power: 0.0-1.0, duration in seconds (None = continuous)
rover.turn_left(power, duration=None)   # power: 0.0-1.0, duration in seconds
rover.turn_right(power, duration=None)  # power: 0.0-1.0, duration in seconds
rover.stop()                             # Stop all motors immediately
rover.drive_instant(left, right)        # Set left/right motors (-1.0 to 1.0) continuously
rover.drive(left, right, duration)      # Drive for duration seconds then stop

# Suspension Positioning
rover.setup_regular_position()  # Standard driving position: servos [90, 90, 150, 30]
rover.setup_sun_position()      # Solar panel orientation: servos [150, 35, 90, 90]
rover.set_servo_positions([a, b, c, d])  # Custom servo positions (0-180 degrees)

# Camera
rover.init_camera(camera_index=0)  # Initialize camera (call once before taking photos)
rover.take_picture(filepath)       # Capture photo, saves to filepath.png, returns path

# Cleanup
rover.cleanup()  # MUST call at end of script to release GPIO resources
```

**Example Script:**
```python
from Robot import *
import time

rover = Rover()
rover.setup_regular_position()
rover.forward(0.7, duration=2)
time.sleep(1)
rover.turn_left(0.5, duration=1)
rover.cleanup()
```
"""

AI_SYSTEM_PROMPT += _ROBOT_API_DOCS


def _broadcast_to_sse(message: str):
    """Push a raw JSON string to every open SSE client queue."""
    with _sse_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # slow client — skip this chunk


def _jetson_ws_thread():
    """Background thread: maintains WebSocket connection to Jetson mapper."""
    global _jetson_ws_connected, _jetson_ws_handle, _map_point_count, _map_seq

    while True:
        try:
            def on_message(ws, message):
                global _map_point_count, _map_seq
                try:
                    data = json.loads(message)
                    if data.get("type") == "chunk":
                        _map_point_count += len(data.get("points", []))
                        _map_seq = data.get("seq", _map_seq)
                    _broadcast_to_sse(message)
                except Exception:
                    pass

            def on_open(ws):
                global _jetson_ws_connected, _jetson_ws_handle
                with _jetson_ws_lock:
                    _jetson_ws_connected = True
                    _jetson_ws_handle = ws
                print("[Map] Connected to Jetson mapper")

            def on_close(ws, code, msg):
                global _jetson_ws_connected, _jetson_ws_handle
                with _jetson_ws_lock:
                    _jetson_ws_connected = False
                    _jetson_ws_handle = None
                print("[Map] Disconnected from Jetson mapper")

            def on_error(ws, error):
                global _jetson_ws_connected
                with _jetson_ws_lock:
                    _jetson_ws_connected = False
                print(f"[Map] WebSocket error: {error}")

            app_ws = ws_client.WebSocketApp(
                JETSON_WS_URL,
                on_message=on_message,
                on_open=on_open,
                on_close=on_close,
                on_error=on_error,
            )
            app_ws.run_forever()
        except Exception as exc:
            print(f"[Map] Connection attempt failed: {exc}")

        time.sleep(5)   # retry delay before reconnecting


class RoverCommand(BaseModel):
    """Structured output the LLM must produce."""
    type: str  # bash_command | edit_file | basic_action | read_file | read_image
    fields: dict


@app.route('/')
def index():
    """Render the main UI page"""
    return render_template('index.html')


@app.route('/send_command', methods=['POST'])
def send_command():
    """Forward command via WiFi to FastAPI server, or save to file for LoRA"""
    try:
        data = request.get_json()
        mode = data.pop('mode', TRANSMISSION_MODE)

        if mode == 'lora':
            return _send_lora(data)
        else:
            return _send_wifi(data)

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def _send_wifi(data):
    """Send command over WiFi to FastAPI server"""
    try:
        timeout = data.pop('timeout', DEFAULT_TIMEOUT)
        response = requests.post(FASTAPI_SERVER_URL, json=data, timeout=timeout)

        return jsonify({
            "success": True,
            "status_code": response.status_code,
            "data": response.json()
        })

    except requests.exceptions.ConnectionError:
        return jsonify({
            "success": False,
            "error": "Could not connect to Rover. Make sure it's powered on and connected to the network."
        }), 503

    except requests.exceptions.Timeout:
        return jsonify({
            "success": False,
            "error": "Request timed out"
        }), 408


def _send_lora(data):
    """Save command as JSON to file for LoRA transmission"""
    try:
        lora_dest = data.pop('lora_destination', LORA_DESTINATION)
        data.pop('timeout', None)

        # Build payload with Recipient field for the LoRA transmitter
        payload = {"Recipient": int(lora_dest)}
        payload.update(data)

        with open(LORA_MESSAGE_PATH, 'w') as f:
            json.dump(payload, f, indent=2)

        return jsonify({
            "success": True,
            "data": {
                "status": "queued",
                "message": f"Payload written to {LORA_MESSAGE_PATH} for LoRA transmission",
                "recipient": int(lora_dest),
                "payload_size": len(json.dumps(payload))
            }
        })

    except (FileNotFoundError, OSError) as e:
        return jsonify({
            "success": False,
            "error": "Cannot write to LoRA module. Please check that the LoRA transmitter is connected and the drive is accessible."
        }), 503
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Failed to write LoRA message file: {str(e)}"
        }), 500


@app.route('/config', methods=['GET', 'POST'])
def config():
    """Get or update server configuration"""
    global FASTAPI_SERVER_URL, DEFAULT_TIMEOUT, TRANSMISSION_MODE, LORA_DESTINATION

    if request.method == 'POST':
        data = request.get_json()
        FASTAPI_SERVER_URL = data.get('server_url', FASTAPI_SERVER_URL)
        DEFAULT_TIMEOUT = data.get('timeout', DEFAULT_TIMEOUT)
        TRANSMISSION_MODE = data.get('mode', TRANSMISSION_MODE)
        LORA_DESTINATION = data.get('lora_destination', LORA_DESTINATION)
        global JETSON_WS_URL
        new_jetson_url = data.get('jetson_ws_url')
        if new_jetson_url:
            JETSON_WS_URL = new_jetson_url
        return jsonify({
            "success": True,
            "server_url": FASTAPI_SERVER_URL,
            "timeout": DEFAULT_TIMEOUT,
            "mode": TRANSMISSION_MODE,
            "lora_destination": LORA_DESTINATION,
            "jetson_ws_url": JETSON_WS_URL
        })

    return jsonify({
        "server_url": FASTAPI_SERVER_URL,
        "timeout": DEFAULT_TIMEOUT,
        "mode": TRANSMISSION_MODE,
        "lora_destination": LORA_DESTINATION,
        "jetson_ws_url": JETSON_WS_URL
    })


@app.route('/map_stream')
def map_stream():
    """SSE endpoint — streams point cloud chunks from Jetson to browser."""
    client_queue: queue.Queue = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_clients.append(client_queue)

    def generate():
        try:
            last_keepalive = time.time()
            while True:
                try:
                    chunk = client_queue.get(timeout=1.0)
                    yield f"data: {chunk}\n\n"
                    last_keepalive = time.time()
                except queue.Empty:
                    if time.time() - last_keepalive > 15:
                        yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                        last_keepalive = time.time()
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                try:
                    _sse_clients.remove(client_queue)
                except ValueError:
                    pass

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


@app.route('/map_control', methods=['POST'])
def map_control():
    """Forward start/stop/clear actions to the Jetson mapper."""
    global _map_point_count, _map_seq

    data = request.get_json()
    action = data.get('action')

    if action == 'clear':
        _map_point_count = 0
        _map_seq = -1

    with _jetson_ws_lock:
        connected = _jetson_ws_connected
        handle = _jetson_ws_handle

    if connected and handle:
        try:
            handle.send(json.dumps({"action": action}))
            return jsonify({"success": True, "action": action})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({"success": False, "error": "Not connected to Jetson"}), 503


@app.route('/map_status')
def map_status():
    """Return current Jetson connection state and map statistics."""
    return jsonify({
        "connected": _jetson_ws_connected,
        "point_count": _map_point_count,
        "seq": _map_seq,
    })


@app.route('/ai_command', methods=['POST'])
def ai_command():
    """Stream an LLM response that decides the command type and body."""
    import time as pytime
    request_start = pytime.time()
    
    try:
        data = request.get_json()
        user_message = data.get('message', '')
        history = data.get('history', [])
        mode = data.get('mode', TRANSMISSION_MODE)

        print(f"[AI] Request received at {pytime.time():.2f}, message length: {len(user_message)}, history items: {len(history)}")

        if not user_message:
            return jsonify({"success": False, "error": "No message provided"}), 400

        # Build messages list: system prompt (with current mode), then history, then user msg
        system_content = AI_SYSTEM_PROMPT + f"\n\n## Current Transmission Mode\nThe current mode is **{mode}**."
        messages = [{"role": "system", "content": system_content}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        prompt_prep_time = pytime.time() - request_start
        print(f"[AI] Prompt prepared in {prompt_prep_time:.2f}s, total context: ~{len(system_content) + sum(len(str(m)) for m in messages):,} chars")
        print(f"[AI] Calling Ollama model: {OLLAMA_MODEL}")
        ollama_start = pytime.time()

        def generate():
            """SSE generator that streams thinking + final JSON."""
            thinking_buffer = ""
            content_buffer = ""
            in_thinking = False
            first_chunk = True

            stream = chat(
                model=OLLAMA_MODEL,
                messages=messages,
                think=True,
                stream=True,
                format=RoverCommand.model_json_schema(),
                options={'num_predict': 2000},  # Limit output length
                keep_alive='1h',  # Keep model loaded for 1 hour to avoid startup delay
            )

            for chunk in stream:
                if first_chunk:
                    first_chunk = False
                    elapsed = pytime.time() - ollama_start
                    print(f"[AI] First chunk received from Ollama after {elapsed:.2f}s")
                
                # Thinking tokens
                if chunk.message.thinking:
                    if not in_thinking:
                        in_thinking = True
                        yield f"data: {json.dumps({'type': 'thinking_start'})}\n\n"
                    thinking_buffer += chunk.message.thinking
                    yield f"data: {json.dumps({'type': 'thinking', 'content': chunk.message.thinking})}\n\n"

                # Content tokens (the actual JSON answer)
                if chunk.message.content:
                    if in_thinking:
                        in_thinking = False
                        yield f"data: {json.dumps({'type': 'thinking_end'})}\n\n"
                    content_buffer += chunk.message.content
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk.message.content})}\n\n"

            # Parse final structured result
            try:
                command = RoverCommand.model_validate_json(content_buffer)
                yield f"data: {json.dumps({'type': 'result', 'command': command.model_dump()})}\n\n"
            except Exception as parse_err:
                yield f"data: {json.dumps({'type': 'error', 'error': f'Failed to parse LLM output: {str(parse_err)}'})}\n\n"

            yield "data: {\"type\": \"done\"}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream')

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# Start Jetson WS client in background (only in reloader worker, not the parent process)
if not os.environ.get("TESTING"):
    _map_thread = threading.Thread(target=_jetson_ws_thread, daemon=True)
    _map_thread.start()

if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    app.run(debug=False, host='0.0.0.0', port=5000)

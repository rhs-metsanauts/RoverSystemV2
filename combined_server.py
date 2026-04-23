from jetson_mapper import ZedMapper, WS_PORT
from flask import Flask, Response, jsonify, render_template
import threading
import asyncio
import websockets

app = Flask(__name__, template_folder="templates")

mapper = ZedMapper()

config_store = {
    "server_url": "http://localhost:8000/execute",
    "timeout": 35,
    "mode": "wifi",
    "lora_destination": 0,
    "jetson_ws_url": f"ws://127.0.0.1:{WS_PORT}",
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video")
def video():
    return Response(mapper.generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route("/map")
def map_json():
    return jsonify(mapper.get_map_2d())


@app.route("/mesh")
def mesh_json():
    return jsonify(mapper.get_mesh())


@app.route("/position")
def position():
    return jsonify(mapper.get_position())


@app.route("/map_status")
def map_status():
    connected = hasattr(mapper, "zed")
    return jsonify({
        "connected": connected,
        "mapping_active": bool(getattr(mapper, "mapping_active", False)),
        "point_count": len(getattr(getattr(mapper, "tracker", None), "_sent", set())),
    })


@app.route("/config", methods=["GET", "POST"])
def config_route():
    from flask import request

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        config_store.update({k: v for k, v in data.items() if v is not None})
    return jsonify(config_store)


def run_flask():
    app.run(host="0.0.0.0", port=5000, threaded=True)


async def async_main():
    # Start websocket server (uses mapper._handle_client)
    async with websockets.serve(mapper._handle_client, "0.0.0.0", WS_PORT):
        await mapper._mapping_loop()


def main():
    # Initialise camera & mapping once
    mapper._init_zed()

    # Start Flask in a background thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    # Run asyncio mapping + websockets loop in main thread
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        mapper.cleanup()


if __name__ == "__main__":
    main()

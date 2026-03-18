from flask import Flask, Response, jsonify
from ZedCamera import ZedCamera

app = Flask(__name__)
zed = ZedCamera()


# --- MJPEG video stream (same pattern as UsbCamera) ---
@app.route('/video')
def video_feed():
    return Response(
        zed.generate_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


# --- One-shot position snapshot ---
@app.route('/position')
def position():
    return jsonify(zed.get_position())


# --- One-shot 2D map snapshot ---
@app.route('/map')
def map_snapshot():
    return jsonify(zed.get_map_2d())


# --- Server-Sent Events: live position stream (~20 Hz) ---
@app.route('/position/stream')
def position_stream():
    return Response(
        zed.generate_position_stream(),
        mimetype='text/event-stream'
    )


# --- Server-Sent Events: live map stream (~1 Hz) ---
@app.route('/map/stream')
def map_stream():
    return Response(
        zed.generate_map_stream(),
        mimetype='text/event-stream'
    )


# --- Persist / reload area map for re-localization ---
@app.route('/map/save', methods=['POST'])
def save_map():
    zed.save_area_map("zed_area.area")
    return jsonify({"status": "saved", "file": "zed_area.area"})

@app.route('/map/load', methods=['POST'])
def load_map():
    zed.load_area_map("zed_area.area")
    return jsonify({"status": "loaded", "file": "zed_area.area"})


if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=5000, threaded=True)
    finally:
        zed.close()
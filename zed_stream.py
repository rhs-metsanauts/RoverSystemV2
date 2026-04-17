"""
zed_stream.py — Live ZED 2i video feed streamer
Run on the Jetson Orin: python3 zed_stream.py

Streams left camera view via TCP to a receiver (e.g. ffplay on a laptop).

Receiver command (on laptop):
    ffplay tcp://192.168.55.1:5001

Requirements: pyzed, ffmpeg installed on the Orin
"""

import subprocess
import pyzed.sl as sl

LAPTOP_IP = "192.168.55.100"  # Ethernet 5 direct connection
PORT = 5001
RESOLUTION = sl.RESOLUTION.HD720
FPS = 30


def main():
    cam = sl.Camera()
    init = sl.InitParameters()
    init.camera_resolution = RESOLUTION
    init.camera_fps = FPS

    status = cam.open(init)
    if status != sl.ERROR_CODE.SUCCESS:
        print(f"Failed to open camera: {status}")
        return

    print(f"Camera opened. Streaming to tcp://0.0.0.0:{PORT} ...")
    print(f"On your laptop run: ffplay tcp://192.168.55.1:{PORT}")

    image = sl.Mat()

    ffmpeg = subprocess.Popen([
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra",
        "-s", "1280x720", "-r", str(FPS),
        "-i", "pipe:0",
        "-f", "mpegts",
        "-codec:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        f"tcp://0.0.0.0:{PORT}?listen"
    ], stdin=subprocess.PIPE)

    try:
        while True:
            if cam.grab() == sl.ERROR_CODE.SUCCESS:
                cam.retrieve_image(image, sl.VIEW.LEFT)
                ffmpeg.stdin.write(image.get_data().tobytes())
    except KeyboardInterrupt:
        print("\nStopping stream...")
    finally:
        ffmpeg.stdin.close()
        ffmpeg.wait()
        cam.close()


if __name__ == "__main__":
    main()

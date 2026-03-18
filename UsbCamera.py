# pan and tilt camera system for the rover controlled by a pi servo hat
from Config import get_config
from ServoHat import get_servo_hat
import cv2


class UsbCamera:
    def __init__(self):
        self.hat = get_servo_hat()
        config = get_config()["usb_camera"]
        self.pan_channel = config["pan"]["channel"]
        self.tilt_channel = config["tilt"]["channel"]
        self.min_pan_angle = config["pan"]["min_angle"]
        self.max_pan_angle = config["pan"]["max_angle"]
        self.min_tilt_angle = config["tilt"]["min_angle"]
        self.max_tilt_angle = config["tilt"]["max_angle"]

        self.cap = cv2.VideoCapture(0)

    def setPanTilt(self, pan, tilt):
        # set the pan and tilt of the camera using the pi servo hat
        # pan and tilt are expected to be in the range of -1.0 to 1.0, where 0.0 is the center position

        raw_pan = pan*(self.max_pan_angle - self.min_pan_angle)/2 + (self.min_pan_angle+self.max_pan_angle)/2
        raw_tilt = tilt*(self.max_tilt_angle - self.min_tilt_angle)/2 + (self.min_tilt_angle+self.max_tilt_angle)/2
        self.hat.move_servo_position(self.pan_channel, raw_pan)
        self.hat.move_servo_position(self.tilt_channel, raw_tilt)

    def generate_frames(self):
        # continuously capture frames from the camera and yield them as JPEG byte streams for streaming over HTTP
        while True:
            ret, frame = self.cap.read()
            _, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
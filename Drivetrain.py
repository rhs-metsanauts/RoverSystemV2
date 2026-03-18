from gpiozero import Motor as _GpioMotor
from gpiozero import Device
from gpiozero.pins.rpigpio import RPiGPIOFactory
import time

from Config import get_config

Device.pin_factory = RPiGPIOFactory()


# ── Motor Class ────────────────────────────────────────────────────────────────

class Motor:
    """
    Controls a single DC motor channel on the XY-160D H-bridge using gpiozero.
    Speed is set as a float from -1.0 (full reverse) to +1.0 (full forward).

    Args:
        in1:     GPIO pin for forward direction
        in2:     GPIO pin for reverse direction
        pwm_pin: GPIO pin for PWM enable (use hardware PWM pins: 18 or 19)
    """

    def __init__(self, in1: int, in2: int, pwm_pin: int):
        self._motor = _GpioMotor(forward=in1, backward=in2, enable=pwm_pin, pwm=True)
        self._speed = 0.0

    @property
    def speed(self) -> float:
        """Current speed, -1.0 to +1.0."""
        return self._speed

    @speed.setter
    def speed(self, value: float):
        self.set_speed(value)

    def set_speed(self, speed: float):
        """
        Set motor speed and direction.
          +1.0 = full forward
           0.0 = brake
          -1.0 = full reverse
        """
        speed = max(-1.0, min(1.0, speed))
        self._speed = speed

        if speed > 0:
            self._motor.forward(speed)
        elif speed < 0:
            self._motor.backward(abs(speed))
        else:
            self.brake()

    def brake(self):
        """Actively brake the motor."""
        self._motor.stop()
        self._speed = 0.0

    def close(self):
        """Release gpiozero resources."""
        self.brake()
        self._motor.close()

    def __repr__(self):
        return f"Motor(speed={self._speed:.2f})"


# ── Drivetrain Class ───────────────────────────────────────────────────────────

class Drivetrain:
    """
    Controls a two-motor differential drivetrain using two Motor instances.
    All movement methods accept an optional duration (seconds). If provided,
    the drivetrain brakes automatically after the duration elapses.

    Args:
        motor_left:  Motor instance for the left side
        motor_right: Motor instance for the right side
    """

    def __init__(self, motor_left: Motor, motor_right: Motor):
        self.left  = motor_left
        self.right = motor_right

    def _run(self, left: float, right: float, duration: float = None):
        """Set speeds, then brake after duration if one is provided."""
        self.left.set_speed(left)
        self.right.set_speed(right)
        if duration is not None:
            time.sleep(duration)
            self.brake()

    # ── Direct control ─────────────────────────────────────────────

    def set_speeds(self, left: float, right: float, duration: float = None):
        """
        Set each motor independently.
          left, right: -1.0 to +1.0
          duration:    seconds to run before braking (None = run indefinitely)
        """
        self._run(left, right, duration)

    # ── High-level movement ────────────────────────────────────────

    def forward(self, speed: float = 1.0, duration: float = None):
        """Drive straight forward."""
        speed = max(0.0, min(1.0, speed))
        self._run(speed, speed, duration)

    def reverse(self, speed: float = 1.0, duration: float = None):
        """Drive straight backward."""
        speed = max(0.0, min(1.0, speed))
        self._run(-speed, -speed, duration)

    def turn_left(self, speed: float = 1.0, duration: float = None):
        """Spin left on the spot."""
        speed = max(0.0, min(1.0, speed))
        self._run(-speed, speed, duration)

    def turn_right(self, speed: float = 1.0, duration: float = None):
        """Spin right on the spot."""
        speed = max(0.0, min(1.0, speed))
        self._run(speed, -speed, duration)

    def steer(self, throttle: float, steering: float, duration: float = None):
        """
        Arcade-style drive mixing for joystick/RC control.

        Args:
            throttle: -1.0 (full reverse) to +1.0 (full forward)
            steering: -1.0 (full left)    to +1.0 (full right)
            duration: seconds to run before braking (None = run indefinitely)

        Examples:
            steer( 1.0,  0.0) → straight forward
            steer( 1.0,  0.5) → forward, curving right
            steer( 0.0,  1.0) → spin right on the spot
            steer(-1.0,  0.0) → straight reverse
        """
        left  = throttle + steering
        right = throttle - steering

        max_val = max(abs(left), abs(right), 1.0)
        left  /= max_val
        right /= max_val

        self._run(left, right, duration)

    def brake(self):
        """Actively brake both motors."""
        self.left.brake()
        self.right.brake()

    @property
    def speeds(self) -> tuple:
        """Returns current (left_speed, right_speed)."""
        return (self.left.speed, self.right.speed)

    def close(self):
        """Release all resources."""
        self.brake()
        self.left.close()
        self.right.close()

    def __repr__(self):
        return f"Drivetrain(left={self.left.speed:.2f}, right={self.right.speed:.2f})"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    drivetrain_config = ["drivetrain"]
    motor_config = drivetrain_config["motors"]
    left_motor_config  = motor_config["left"]
    right_motor_config = motor_config["right"]

    motor_left = Motor(
        in1=left_motor_config["in1"],
        in2=left_motor_config["in2"],
        pwm_pin=left_motor_config["pwm_pin"],
    )
    motor_right = Motor(
        in1=right_motor_config["in1"],
        in2=right_motor_config["in2"],
        pwm_pin=right_motor_config["pwm_pin"],
    )

    drive = Drivetrain(motor_left, motor_right)

    try:
        drive.forward(speed=0.8, duration=2)
        drive.reverse(speed=0.5, duration=2)
        drive.turn_left(speed=0.7, duration=1)
        drive.turn_right(speed=0.7, duration=1)
        drive.steer(throttle=0.8, steering=0.4, duration=2)
        drive.steer(throttle=0.8, steering=-0.4, duration=2)
        drive.set_speeds(left=0.6, right=-0.3, duration=2)

    finally:
        drive.close()
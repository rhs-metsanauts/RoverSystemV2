import pi_servo_hat
import qwiic_i2c
from qwiic_i2c.linux_i2c import LinuxI2C

from Config import get_config

_shared_hat = None
_PCA9685_MODE1_REGISTER = 0x00


def _get_servo_hat_i2c_settings():
    servo_hat_config = get_config().get("servo_hat", {})

    i2c_bus = int(servo_hat_config.get("i2c_bus", 7))
    i2c_address = servo_hat_config.get("i2c_address", 0x40)

    if isinstance(i2c_address, str):
        i2c_address = int(i2c_address, 0)

    return i2c_bus, int(i2c_address)


def _probe_servo_hat(i2c_driver, i2c_address):
    """Probe PCA9685 reliably.

    qwiic_i2c's isDeviceConnected uses SMBus write_quick, which can report
    false negatives with PCA9685 on some platforms. Fall back to reading MODE1.
    """

    if i2c_driver.isDeviceConnected(i2c_address):
        return True

    try:
        i2c_driver.readByte(i2c_address, _PCA9685_MODE1_REGISTER)
        return True
    except Exception:
        return False


def get_servo_hat():
    global _shared_hat

    if _shared_hat is None:
        i2c_bus, i2c_address = _get_servo_hat_i2c_settings()

        try:
            i2c_driver = LinuxI2C(i2c_bus)
            if i2c_driver.i2cbus is None:
                raise RuntimeError(
                    f"Failed to open /dev/i2c-{i2c_bus}. "
                    "Verify I2C is enabled and your user has i2c permissions."
                )

            # pi_servo_hat uses qwiic_i2c.getI2CDriver() internally and defaults to bus 1.
            # Override its default driver so Jetson bus selection is explicit and reliable.
            qwiic_i2c._default_driver = i2c_driver

            if not _probe_servo_hat(i2c_driver, i2c_address):
                raise RuntimeError(
                    f"Servo driver not detected at address 0x{i2c_address:02X} on /dev/i2c-{i2c_bus}. "
                    f"Verify SDA/SCL wiring and run: i2cdetect -y -r {i2c_bus}"
                )

            _shared_hat = pi_servo_hat.PiServoHat(address=i2c_address)
            _shared_hat.restart()
        except OSError as e:
            raise RuntimeError(
                f"Failed to open servo driver at 0x{i2c_address:02X} on /dev/i2c-{i2c_bus}: {e}"
            ) from e

    return _shared_hat


def cleanup_servo_hat():
    """Clean up and release the servo hat resource"""
    global _shared_hat
    if _shared_hat is not None:
        _shared_hat = None
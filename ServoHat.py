import pi_servo_hat

_shared_hat = None


def get_servo_hat():
    global _shared_hat

    if _shared_hat is None:
        _shared_hat = pi_servo_hat.PiServoHat()
        _shared_hat.restart()

    return _shared_hat
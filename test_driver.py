import time

import Jetson.GPIO as GPIO

from Config import get_config

STEP_SECONDS = 2.0


def _profile_from_config_bcm():
    drivetrain_cfg = get_config().get("drivetrain", {}).get("motors", {})
    left_cfg = drivetrain_cfg.get("left", {})
    right_cfg = drivetrain_cfg.get("right", {})

    return {
        "name": "config-bcm",
        "mode": GPIO.BCM,
        "pins": {
            "ena": int(left_cfg.get("pwm_pin", 18)),
            "in1": int(left_cfg.get("in1", 17)),
            "in2": int(left_cfg.get("in2", 27)),
            "enb": int(right_cfg.get("pwm_pin", 19)),
            "in3": int(right_cfg.get("in1", 22)),
            "in4": int(right_cfg.get("in2", 23)),
        },
    }


PIN_PROFILES = [
    _profile_from_config_bcm(),
    {
        "name": "config-board",
        "mode": GPIO.BOARD,
        "pins": {"ena": 12, "in1": 11, "in2": 13, "enb": 35, "in3": 15, "in4": 16},
    },
    {
        "name": "legacy-board",
        "mode": GPIO.BOARD,
        "pins": {"ena": 15, "in1": 11, "in2": 13, "enb": 33, "in3": 16, "in4": 18},
    },
]

LEGACY_WIRING = {"ena": 15, "in1": 11, "in2": 13, "enb": 33, "in3": 16, "in4": 18}


def _setup_outputs(pins):
    for pin in pins.values():
        GPIO.setup(pin, GPIO.OUT)


def _stop_all(pins):
    GPIO.output(pins["ena"], GPIO.LOW)
    GPIO.output(pins["enb"], GPIO.LOW)
    GPIO.output(pins["in1"], GPIO.LOW)
    GPIO.output(pins["in2"], GPIO.LOW)
    GPIO.output(pins["in3"], GPIO.LOW)
    GPIO.output(pins["in4"], GPIO.LOW)


def _drive_a(pins, forward):
    GPIO.output(pins["in1"], GPIO.HIGH if forward else GPIO.LOW)
    GPIO.output(pins["in2"], GPIO.LOW if forward else GPIO.HIGH)
    GPIO.output(pins["ena"], GPIO.HIGH)


def _drive_b(pins, forward):
    GPIO.output(pins["in3"], GPIO.HIGH if forward else GPIO.LOW)
    GPIO.output(pins["in4"], GPIO.LOW if forward else GPIO.HIGH)
    GPIO.output(pins["enb"], GPIO.HIGH)


def run_profile(profile):
    name = profile["name"]
    mode = profile["mode"]
    pins = profile["pins"]

    GPIO.setwarnings(False)
    GPIO.setmode(mode)
    _setup_outputs(pins)
    _stop_all(pins)

    print(f"=== Profile: {name} ===")
    print(
        "Pins -> "
        f"ENA={pins['ena']}, IN1={pins['in1']}, IN2={pins['in2']}, "
        f"ENB={pins['enb']}, IN3={pins['in3']}, IN4={pins['in4']}"
    )

    print("Motor A forward...")
    _drive_a(pins, forward=True)
    time.sleep(STEP_SECONDS)
    _stop_all(pins)
    time.sleep(0.5)

    print("Motor A reverse...")
    _drive_a(pins, forward=False)
    time.sleep(STEP_SECONDS)
    _stop_all(pins)
    time.sleep(0.5)

    print("Motor B forward...")
    _drive_b(pins, forward=True)
    time.sleep(STEP_SECONDS)
    _stop_all(pins)
    time.sleep(0.5)

    print("Motor B reverse...")
    _drive_b(pins, forward=False)
    time.sleep(STEP_SECONDS)
    _stop_all(pins)

    print(f"Profile {name} done.")


def run_legacy_exhaustive():
    pins = LEGACY_WIRING
    combos = [
        (GPIO.HIGH, GPIO.LOW, "INx=HIGH, INy=LOW"),
        (GPIO.LOW, GPIO.HIGH, "INx=LOW, INy=HIGH"),
        (GPIO.HIGH, GPIO.HIGH, "INx=HIGH, INy=HIGH"),
        (GPIO.LOW, GPIO.LOW, "INx=LOW, INy=LOW"),
    ]

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    _setup_outputs(pins)
    _stop_all(pins)

    print("=== Exhaustive Legacy Wiring Test ===")
    print("Pins -> ENA=15, IN1=11, IN2=13, ENB=33, IN3=16, IN4=18")

    for en_level, en_label in ((GPIO.HIGH, "ENABLE=HIGH"), (GPIO.LOW, "ENABLE=LOW")):
        print(f"-- {en_label} pass --")

        for a_in1, a_in2, a_desc in combos:
            _stop_all(pins)
            GPIO.output(pins["in1"], a_in1)
            GPIO.output(pins["in2"], a_in2)
            GPIO.output(pins["ena"], en_level)
            print(f"Motor A: {a_desc}")
            time.sleep(1.5)

        _stop_all(pins)
        time.sleep(0.5)

        for b_in1, b_in2, b_desc in combos:
            _stop_all(pins)
            GPIO.output(pins["in3"], b_in1)
            GPIO.output(pins["in4"], b_in2)
            GPIO.output(pins["enb"], en_level)
            print(f"Motor B: {b_desc}")
            time.sleep(1.5)

        _stop_all(pins)
        time.sleep(0.7)

    print("Exhaustive legacy wiring test complete.")


if __name__ == "__main__":
    for profile in PIN_PROFILES:
        try:
            run_profile(profile)
        except Exception as e:
            print(f"Profile {profile['name']} failed: {e}")
        finally:
            try:
                GPIO.cleanup()
            except Exception:
                pass
            time.sleep(0.7)

    try:
        run_legacy_exhaustive()
    finally:
        try:
            GPIO.cleanup()
        except Exception:
            pass

    print("All profiles complete.")
from Config import get_config
from ServoHat import get_servo_hat


class Drivetrain:
    def __init__(self):
        self.hat = get_servo_hat()
        config = get_config()["drivetrain"]
        self.motors = config["motors"]
        self.left_channel  = self.motors["left"]["channel"]
        self.right_channel = self.motors["right"]["channel"]
        self._arm()

    def _arm(self):
        import time
        # Send neutral (90°) and hold it so the ESC arms
        self.hat.move_servo_position(self.left_channel,  126)
        self.hat.move_servo_position(self.right_channel, 126)
        time.sleep(0.02)
        self.hat.move_servo_position(self.left_channel,  54)
        self.hat.move_servo_position(self.right_channel, 54)
        time.sleep(0.02)
        self.setPowers(0, 0)  # Ensure motors are stopped after arming
        
        
    def setPowers(self, left_power, right_power):
        # set the power of the left and right motors using the pi servo hat
        # left_power and right_power are expected to be in the range of -1.0 to 1.0, where 0.0 is stopped, -1.0 is full reverse, and 1.0 is full forward
        self.hat.move_servo_position(self.left_channel, left_power*50 +50)
        self.hat.move_servo_position(self.right_channel, -right_power*50 +50)


if __name__ == "__main__":
    import time
    drivetrain = Drivetrain()
    while True:
        powers = input("Enter left and right power (e.g. '0.5 -0.5' for half forward left and half reverse right): ")
        try:
            left_power, right_power = map(float, powers.split())
            drivetrain.setPowers(left_power, right_power)
        except ValueError:
            print("Invalid input. Please enter two numbers separated by a space.")
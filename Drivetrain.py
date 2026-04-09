from Config import get_config
from ServoHat import get_servo_hat


class Drivetrain:
    def __init__(self):
        self.hat = get_servo_hat()
        config = get_config()["drivetrain"]
        self.motors = config["motors"]
        self.left_channel = self.motors["left"]["channel"]
        self.right_channel = self.motors["right"]["channel"]
    
    def setPowers(self, left_power, right_power):
        # set the power of the left and right motors using the pi servo hat
        # left_power and right_power are expected to be in the range of -1.0 to 1.0, where 0.0 is stopped, -1.0 is full reverse, and 1.0 is full forward
        self.hat.move_servo_position(self.left_channel, 90 + left_power*90)
        self.hat.move_servo_position(self.right_channel, 90 + right_power*90)



if __name__ == "__main__":
    import time
    drivetrain = Drivetrain()
    drivetrain.setPowers(1, 1)
    time.sleep(2)
    drivetrain.setPowers(0, 0)
from Config import get_config
from ServoHat import get_servo_hat


class RockerBogie:
    def __init__(self):
        self.hat = get_servo_hat()
        config = get_config()["rocker_bogie"]
        self.channels = config["rocker_bogie"]["channels"]
        self.sun_position = config["sun_position"]
        self.regular_position = config["regular_position"]
    
    def setPositions(self, positions):
        for index, pos in zip(self.channels, positions):
            self.hat.move_servo_position(index, pos)

    def toSunPosition(self):
        self.setPositions(self.sun_position)
    
    def toRegularPosition(self):
        self.setPositions(self.regular_position)
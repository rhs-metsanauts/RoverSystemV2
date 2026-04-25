from RockerBogie import RockerBogie
from Drivetrain import Drivetrain
import time

class Rover:
    def __init__(self):
        self.rocker_bogie = RockerBogie()
        self.drivetrain = Drivetrain()
        self.toRegularPosition()
        self.stop()
    def toSunPosition(self):
        self.rocker_bogie.toSunPosition()
    def toRegularPosition(self):
        self.rocker_bogie.toRegularPosition()
    def setPowers(self, left_power, right_power):
        self.drivetrain.setPowers(left_power, right_power)
    def forward(self, power):
        self.setPowers(power, power)
    def backward(self, power):
        self.setPowers(-power, -power) 
    def turnLeft(self, power):
        self.setPowers(power, -power)
    def turnRight(self, power):
        self.setPowers(-power, power)
    def stop(self):
        self.setPowers(0, 0)

    def forwardForDuration(self, power, duration):
        self.forward(power)
        time.sleep(duration)
        self.stop()
    def backwardForDuration(self, power, duration):
        self.backward(power)
        time.sleep(duration)
        self.stop()
    def turnLeftForDuration(self, power, duration):
        self.turnLeft(power)
        time.sleep(duration)
        self.stop()
    def turnRightForDuration(self, power, duration):
        self.turnRight(power)
        time.sleep(duration)
        self.stop()

if __name__ == "__main__":
    import time
    rover = Rover()
    time.sleep(2)
    rover.forwardForDuration(1, 2)
    time.sleep(1)
    rover.turnLeftForDuration(1, 1)
    time.sleep(1)
    rover.turnRightForDuration(1, 1)
    rover.stop()
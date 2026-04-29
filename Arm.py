from Config import get_config
from ServoHat import get_servo_hat


class Arm:
    def __init__(self):
        self.hat = get_servo_hat()
    
    def stand(self, positions):
        self.hat.move_servo_position(4, positions[0])
        self.hat.move_servo_position(5, positions[1])
        self.hat.move_servo_position(6, positions[2])
        self.hat.move_servo_position(7, positions[3])

    def retracted(self):
        self.stand([80, 100, 50, 130])
    
    def extended(self):
        self.stand([80, 5, 50, 110])

if __name__ == "__main__":
    from RockerBogie import RockerBogie
    rockerbogie = RockerBogie()
    rockerbogie.toRegularPosition()
    import time
    arm = Arm()
    arm.retracted()
    time.sleep(2)
    arm.extended()
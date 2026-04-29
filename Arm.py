from Config import get_config
from ServoHat import get_servo_hat


class Arm:
    def __init__(self):
        self.hat = get_servo_hat()
    
    def stand(self, positions):
        self.hat.move_servo_position(5, positions[0])
        self.hat.move_servo_position(6, positions[1])
        self.hat.move_servo_position(7, positions[2])
        self.hat.move_servo_position(8, positions[3])

    def retracted(self):
        self.stand([0.5, 0.5, 0.5, 0.5])
    
    def extended(self):
        self.stand([0.8, 0.2, 0.8, 0.2])

if __name__ == "__main__":
    import time
    arm = Arm()
    arm.extended()
    time.sleep(2)
    #arm.retracted()
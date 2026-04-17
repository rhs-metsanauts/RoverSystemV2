import pyzed.sl as sl
import numpy as np

cam = sl.Camera()
init = sl.InitParameters()
init.coordinate_units = sl.UNIT.METER
init.depth_mode = sl.DEPTH_MODE.PERFORMANCE
cam.open(init)

point_cloud = sl.Mat()

print("Grabbing 5 frames, checking for valid depth...")
for i in range(5):
    if cam.grab() == sl.ERROR_CODE.SUCCESS:
        cam.retrieve_measure(point_cloud, sl.MEASURE.XYZRGBA)
        data = point_cloud.get_data()
        valid = np.isfinite(data[:, :, 0]).sum()
        print(f"Frame {i}: {valid} valid depth pixels out of {data.shape[0]*data.shape[1]}")

cam.close()

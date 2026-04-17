import pyzed.sl as sl
import time

cam = sl.Camera()
init = sl.InitParameters()
init.coordinate_units = sl.UNIT.METER
init.depth_mode = sl.DEPTH_MODE.PERFORMANCE
cam.open(init)

cam.enable_positional_tracking(sl.PositionalTrackingParameters())

mp = sl.SpatialMappingParameters()
mp.resolution_meter = 0.05
mp.range_meter = 5.0
mp.save_texture = False
mp.use_chunk_only = False  # full mesh mode
cam.enable_spatial_mapping(mp)

mesh = sl.Mesh()
pose = sl.Pose()

print("Scanning - move camera slowly over textured surface...")
for i in range(300):
    if cam.grab() == sl.ERROR_CODE.SUCCESS:
        if i % 30 == 0:
            cam.request_spatial_map_async()

        status = cam.get_spatial_map_request_status_async()
        if status == sl.ERROR_CODE.SUCCESS:
            cam.retrieve_spatial_map_async(mesh)
            verts = mesh.vertices
            print(f"Frame {i}: {len(verts) if verts is not None else 0} vertices")

cam.close()

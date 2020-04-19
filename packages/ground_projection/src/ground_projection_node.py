#!/usr/bin/env python

# from cv_bridge import CvBridge, CvBridgeError
# from duckietown_msgs.msg import (Segment, SegmentList)
# import duckietown_utils as dtu
# from ground_projection.ground_projection_interface import GroundProjection, \
#     get_ground_projection_geometry_for_robot
# from ground_projection.srv import EstimateHomography, EstimateHomographyResponse, GetGroundCoord, GetGroundCoordResponse, GetImageCoord, GetImageCoordResponse  #@UnresolvedImport
# import rospy
# from sensor_msgs.msg import (Image, CameraInfo)

import numpy as np
import cv2
from cv_bridge import CvBridge

from duckietown import DTROS
from ground_projection import Point, GroundProjectionGeometry
from image_geometry import PinholeCameraModel

from sensor_msgs.msg import CameraInfo, CompressedImage
from geometry_msgs.msg import Point as PointMsg

#######################################################################
#
# - Intrinsic must be upadtable
# - Extrinsic loaded from file
# - Take line segments from an unrectified image
# - Rectify them
# - Project them on the ground
#
#######################################################################


class GroundProjectionNode(DTROS):

    def __init__(self, node_name):
        # Initialize the DTROS parent class
        super(CameraNode, self).__init__(node_name=node_name)

        self.bridge = CvBridge()
        self.pcm = None
        self.ground_projector = None
        self.homography = self.load_extrinsics()
        self.first_processing_done = False

        # subscribers
        self.sub_camera_info = self.subscriber("~camera_info", CameraInfo, self.cb_camera_info, queue_size=1)
        self.sub_lineseglist_ = self.subscriber("~lineseglist_in", SegmentList, self.lineseglist_cb, queue_size=1)

        # publishers
        self.pub_lineseglist = self.publisher("~lineseglist_out", SegmentList, queue_size=1)
        self.pub_debug_img = self.publisher("~debug/ground_projection_image/compressed", CompressedImage, queue_size=1)

        self.robot_name = robot_name

        self.gp = GroundProjection(self.robot_name)

        self.bridge = CvBridge()

        # self.gpg = get_ground_projection_geometry_for_robot(self.robot_name)

        # self.image_channel_name = "image_raw"

        # Seems to be never used:
        # self.service_homog_ = rospy.Service("~estimate_homography", EstimateHomography, self.estimate_homography_cb)
        # self.service_gnd_coord_ = rospy.Service("~get_ground_coordinate", GetGroundCoord, self.get_ground_coordinate_cb)
        # self.service_img_coord_ = rospy.Service("~get_image_coordinate", GetImageCoord, self.get_image_coordinate_cb)

    def cb_camera_info(self, msg):
        self.pcm = PinholeCameraModel()
        self.pcm.fromCameraInfo(msg)
        self.ground_projector = GroundProjectionGeometry(im_width=msg.width,
                                                         im_height=msg.height,
                                                         homography=self.homography)

    def pixel_msg_to_ground_msg(self, point_msg):
        # normalized coordinates to pixel:
        norm_pt = Point.from_message(point_msg)
        pixel = self.ground_projector.vector_to_pixel(pixel)
        # rectify
        rect = Point(*list(self.pcm.rectifyPoint([pixel.x, pixel.y])))
        # convert to Point
        rect_pt = Point.from_message(rect)
        # project on ground
        ground_pt = self.ground_projector.pixel_to_ground(rect_pt)
        # point to message
        ground_pt_msg = PointMsg()
        ground_pt_msg.x = ground_pt.x
        ground_pt_msg.y = ground_pt.y
        ground_pt_msg.z = ground_pt.z

        return ground_pt_msg

    def lineseglist_cb(self, seglist_msg):
        if self.pcm is None or self.ground_projector:
            seglist_out = SegmentList()
            seglist_out.header = seglist_msg.header
            for received_segment in seglist_msg.segments:
                new_segment = Segment()
                new_segment.points[0] = self.pixel_msg_to_ground_msg(received_segment.pixels_normalized[0])
                new_segment.points[1] = self.pixel_msg_to_ground_msg(received_segment.pixels_normalized[1])
                new_segment.color = received_segment.color
                # TODO what about normal and points
                seglist_out.segments.append(new_segment)
            self.pub_lineseglist.publish(seglist_out)

            if not self.first_processing_done:
                self.log('First projected segments published.')
                self.first_processing_done = True

            if self.pub_debug_img.get_num_connections() > 0:
                debug_image_msg = self.bridge.cv2_to_compressed_imgmsg(self.debug_image(seglist_out))
                debug_image_msg.header = seglist_out.header
                self.pub_debug_img.publish(debug_image_msg)
        else:
            self.log('Waiting for a CameraInfo message', 'warn')

    # def get_ground_coordinate_cb(self, req):
    #     return GetGroundCoordResponse(self.pixel_msg_to_ground_msg(req.uv))
    #
    # def get_image_coordinate_cb(self, req):
    #     return GetImageCoordResponse(self.gpg.ground2pixel(req.gp))
    #
    # def estimate_homography_cb(self, req):
    #     rospy.loginfo("Estimating homography")
    #     rospy.loginfo("Waiting for raw image")
    #     img_msg = rospy.wait_for_message("/" + self.robot_name + "/camera_node/image/raw", Image)
    #     rospy.loginfo("Got raw image")
    #     try:
    #         cv_image = self.bridge.imgmsg_to_cv2(img_msg, desired_encoding="bgr8")
    #     except CvBridgeError as e:
    #         rospy.logerr(e)
    #     self.gp.estimate_homography(cv_image)
    #     rospy.loginfo("wrote homography")
    #     return EstimateHomographyResponse()

    def load_extrinsics(self):
        # load intrinsic calibration
        cali_file_folder = '/data/config/calibrations/camera_extrinsics/'
        cali_file = cali_file_folder + rospy.get_namespace().strip("/") + ".yaml"

        # Locate calibration yaml file or use the default otherwise
        if not os.path.isfile(cali_file):
            self.log("Can't find calibration file: %s.\n Using default calibration instead."
                     % cali_file, 'warn')
            cali_file = (cali_file_folder + "default.yaml")

        # Shutdown if no calibration file not found
        if not os.path.isfile(cali_file):
            self.log("Found no calibration file ... aborting", 'err')
            rospy.signal_shutdown()

        stream = file(cali_file, 'r')

        # TODO; catch errors
        calib_data = yaml.load(stream)

        return calib_data['homography']

    def debug_image(self, seg_list):
        image = np.ones((200, 200, 3), np.uint8) * 128

        color_map = {Segment.WHITE: cv2.CV_RGB(255, 255, 255),
                     Segment.RED: cv2.CV_RGB(255, 0, 0),
                     Segment.YELLOW: cv2.CV_RGB(255, 255, 0)}

        for segment in seg_list.segments:
            cv2.line(image,
                     pt1=((segment.points[0].x*100)+100, (new_segment.points[0].y*100)+100),
                     pt2=((segment.points[1].x*100)+100, (new_segment.points[1].y*100)+100),
                     color=color_map.get(segment.color, default=cv2.CV_RGB(0, 0, 0)),
                     thickness=1)

        return image

if __name__ == '__main__':
    ground_projection_node = GroundProjectionNode(node_name='ground_projection')
    rospy.spin()

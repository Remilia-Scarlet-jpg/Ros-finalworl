#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟相机（sim_camera）—— 只在没真机时用

发一张合成的彩色图到指定话题，让拍照节点有图可抓。
图里画了时间戳和一个色块，方便确认抓到的确实是这一刻的画面。

用法：
    rosrun spark_deliver sim_camera.py
    rosrun spark_deliver sim_camera.py _image_topic:=/camera/rgb/image_raw
"""

from __future__ import print_function

import time

import cv2
import numpy as np
import rospy
from cv_bridge import CvBridge
from sensor_msgs.msg import Image


class SimCamera(object):
    def __init__(self):
        rospy.init_node('sim_camera')

        self.topic = rospy.get_param('~image_topic', '/camera/color/image_raw')
        self.width = int(rospy.get_param('~width', 640))
        self.height = int(rospy.get_param('~height', 480))
        self.rate = float(rospy.get_param('~rate', 10.0))

        self.bridge = CvBridge()
        self.pub = rospy.Publisher(self.topic, Image, queue_size=2)
        rospy.loginfo('模拟相机：%s  %dx%d @ %.1fHz',
                      self.topic, self.width, self.height, self.rate)

    def spin(self):
        rate = rospy.Rate(self.rate)
        while not rospy.is_shutdown():
            frame = np.full((self.height, self.width, 3), 232, dtype=np.uint8)
            cv2.rectangle(frame, (40, 40), (self.width - 40, self.height - 40),
                          (200, 200, 200), 2)

            box_x = 80 + int((self.width - 240) *
                             (0.5 + 0.5 * np.sin(rospy.get_time())))
            cv2.rectangle(frame, (box_x, 160), (box_x + 140, 320),
                          (60, 90, 200), -1)

            text = time.strftime('%H:%M:%S') + '  SIM'
            cv2.putText(frame, text, (60, 100), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (40, 40, 40), 2)
            cv2.putText(frame, 'spark_deliver test frame', (60, 400),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2)

            msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
            msg.header.stamp = rospy.Time.now()
            msg.header.frame_id = 'camera_rgb_optical_frame'
            self.pub.publish(msg)
            rate.sleep()


if __name__ == '__main__':
    try:
        SimCamera().spin()
    except rospy.ROSInterruptException:
        pass

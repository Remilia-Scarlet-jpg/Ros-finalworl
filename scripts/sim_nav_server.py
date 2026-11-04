#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 move_base（sim_nav_server）—— 只在没真机时用

起一个假的 move_base 动作服务器，收到目标就假装走一段路，然后返回成功。
配合 sim_camera.py 可以在没有机器人、没有地图的情况下把送物流程跑通。

用法：
    rosrun spark_deliver sim_nav_server.py
    rosrun spark_deliver sim_nav_server.py _duration:=3.0 _fail_rate:=0.2
"""

from __future__ import print_function

import time

import actionlib
import rospy
from move_base_msgs.msg import MoveBaseAction, MoveBaseResult


class SimNavServer(object):
    def __init__(self):
        rospy.init_node('sim_nav_server')

        self.duration = float(rospy.get_param('~duration', 2.0))
        self.fail_rate = float(rospy.get_param('~fail_rate', 0.0))

        self.server = actionlib.SimpleActionServer(
            'move_base', MoveBaseAction, execute_cb=self._execute, auto_start=False)
        self.server.start()
        rospy.loginfo('模拟 move_base 已启动：每段 %.1fs，失败率 %.2f',
                      self.duration, self.fail_rate)

    def _execute(self, goal):
        pose = goal.target_pose.pose.position
        rospy.loginfo('收到目标 (%.2f, %.2f)，开始"行驶"', pose.x, pose.y)

        deadline = time.time() + self.duration
        while time.time() < deadline:
            if self.server.is_preempt_requested():
                self.server.set_preempted()
                rospy.logwarn('目标被取消')
                return
            time.sleep(0.05)

        if self.fail_rate > 0.0 and rospy.get_time() % 1.0 < self.fail_rate:
            self.server.set_aborted(MoveBaseResult())
            rospy.logwarn('模拟导航失败 (%.2f, %.2f)', pose.x, pose.y)
            return

        self.server.set_succeeded(MoveBaseResult())
        rospy.loginfo('到达 (%.2f, %.2f)', pose.x, pose.y)


if __name__ == '__main__':
    try:
        SimNavServer()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass

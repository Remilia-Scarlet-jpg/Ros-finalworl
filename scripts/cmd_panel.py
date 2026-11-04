#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终端操作面板（cmd_panel）

不想敲 rostopic 的时候用这个：列出地点、按编号送物、确认取件、取消、巡拍。
在触摸屏没配好的时候也能完整演示。

用法：
    rosrun spark_deliver cmd_panel.py
"""

from __future__ import print_function

import json
import os
import subprocess
import sys

import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger

COMMAND_TOPIC = '/spark_deliver/command'
STATUS_TOPIC = '/spark_deliver/status'

LINE = '-' * 56


class Panel(object):
    def __init__(self):
        rospy.init_node('cmd_panel', anonymous=True)
        self.status = {}
        self.cmd_pub = rospy.Publisher(COMMAND_TOPIC, String, queue_size=10)
        rospy.Subscriber(STATUS_TOPIC, String, self._status_cb, queue_size=10)

    def _status_cb(self, msg):
        try:
            self.status = json.loads(msg.data)
        except ValueError:
            self.status = {}

    def send(self, command):
        self.cmd_pub.publish(String(data=command))
        rospy.loginfo('已发送指令：%s', command)

    def call(self, name):
        try:
            rospy.wait_for_service(name, timeout=2.0)
            proxy = rospy.ServiceProxy(name, Trigger)
            response = proxy()
            print('  %s' % response.message)
        except Exception as exc:  # noqa: BLE001
            print('  调用 %s 失败：%s' % (name, exc))

    def show(self):
        state = self.status.get('state', '-')
        text = self.status.get('text', '')
        place = self.status.get('place', '')
        places = self.status.get('places', [])

        print('')
        print(LINE)
        print('当前状态：%s  %s %s' % (state, text, ('@' + place) if place else ''))
        print(LINE)
        for index, name in enumerate(places, start=1):
            print('  %d. 送到 %s' % (index, name))
        print('  c. 确认取件     x. 取消任务')
        print('  p. 巡拍一圈     h. 回出发点')
        print('  r. 重建拍照索引 q. 退出')
        print(LINE)


def main():
    panel = Panel()
    rospy.sleep(1.0)

    while not rospy.is_shutdown():
        panel.show()
        try:
            raw = input('请选择 > ').strip()
        except (EOFError, KeyboardInterrupt):
            print('')
            break

        if not raw:
            continue
        low = raw.lower()

        if low in ('q', 'quit', 'exit'):
            break
        if low in ('c', 'confirm'):
            panel.call('/spark_deliver/confirm')
            continue
        if low in ('x', 'cancel'):
            panel.call('/spark_deliver/cancel')
            continue
        if low in ('p', 'photo'):
            panel.send('photo')
            continue
        if low in ('h', 'home'):
            panel.send('back')
            continue
        if low in ('r', 'report'):
            _rebuild_report()
            continue

        if raw.isdigit():
            panel.send(raw)
            rospy.sleep(1.0)
            continue

        print('  看不懂这个选项：%s' % raw)

    return 0


def _rebuild_report():
    save_dir = os.path.expanduser(
        rospy.get_param('/photo/save_dir', '~/.spark_deliver/photos'))

    script = None
    try:
        import rospkg
        candidate = os.path.join(rospkg.RosPack().get_path('spark_deliver'),
                                 'scripts', 'make_report.py')
        if os.path.isfile(candidate):
            script = candidate
    except Exception:  # noqa: BLE001
        pass
    if script is None:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'make_report.py')
        script = local if os.path.isfile(local) else None

    if script is None:
        print('  找不到 make_report.py')
        return

    try:
        output = subprocess.check_output(
            [sys.executable, script, '--dir', save_dir],
            stderr=subprocess.STDOUT, timeout=60)
        print('  %s' % output.decode('utf-8', 'ignore').strip())
    except Exception as exc:  # noqa: BLE001
        print('  生成索引失败：%s' % exc)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except rospy.ROSInterruptException:
        pass

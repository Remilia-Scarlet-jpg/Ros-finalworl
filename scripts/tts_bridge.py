#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音播报桥（tts_bridge）

订阅 /voice/tts，用本机 ekho 念出来。这样送物节点只负责发文本，
换 TTS 引擎时不用改业务代码。

如果你已经有别的播报节点（spark_voice/ekho_tts.py、百度 TTS 等），
启动 launch 时把 start_tts 设成 false 即可，避免两路同时出声。

用法：
    rosrun spark_deliver tts_bridge.py
    rostopic pub -1 /voice/tts std_msgs/String "data: '测试一下'"
"""

from __future__ import print_function

import queue
import subprocess
import threading

import rospy
from std_msgs.msg import String


class TtsBridge(object):
    def __init__(self):
        rospy.init_node('tts_bridge')

        self.engine = rospy.get_param('~engine', 'ekho')
        self.voice = rospy.get_param('~voice', 'Mandarin')
        self.speed = int(rospy.get_param('~speed', 0))
        self.timeout = float(rospy.get_param('~play_timeout', 20.0))

        self.queue = queue.Queue(maxsize=4)
        rospy.Subscriber('/voice/tts', String, self._cb, queue_size=10)

        self.worker = threading.Thread(target=self._run)
        self.worker.daemon = True
        self.worker.start()

        rospy.loginfo('播报桥就绪：引擎=%s 音色=%s', self.engine, self.voice)

    def _cb(self, msg):
        text = (msg.data or '').strip()
        if not text:
            return
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
        self.queue.put(text)

    def _run(self):
        while not rospy.is_shutdown():
            try:
                text = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._speak(text)
            except Exception as exc:  # noqa: BLE001
                rospy.logwarn('播报失败：%s', exc)

    def _speak(self, text):
        rospy.loginfo('TTS: %s', text)
        if self.engine in ('none', 'off', ''):
            return
        command = ['ekho', '-v', self.voice, '-s', str(self.speed), text]
        subprocess.call(command, timeout=self.timeout)


if __name__ == '__main__':
    try:
        TtsBridge()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass

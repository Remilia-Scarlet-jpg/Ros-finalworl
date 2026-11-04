#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拍照存档节点（photo_node）

收到 /spark_deliver/photo_event 就抓一帧彩色图存盘，并更新网页索引。

存盘结构：
    ~/.spark_deliver/photos/
    ├── 2026-11-02/
    │   ├── 143012_前台.jpg
    │   └── 143145_会议室.jpg
    ├── thumbs/            # 缩略图，网页用
    ├── meta.jsonl         # 每行一条记录
    └── index.html         # 拍照索引页，可直接浏览器打开

本节点只读相机话题，不修改任何其他功能包。
"""

from __future__ import print_function

import json
import os
import subprocess
import sys
import threading
import time

import cv2
import rospy
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image
from std_msgs.msg import String

EVENT_TOPIC = '/spark_deliver/photo_event'
SAVED_TOPIC = '/spark_deliver/photo_saved'


def find_report_script():
    """定位 make_report.py。

    注意：devel 环境下的 rosrun 脚本是 catkin 生成的转发脚本，
    不能直接 import 同目录的模块，所以这里用子进程调用。
    """
    try:
        import rospkg
        path = os.path.join(rospkg.RosPack().get_path('spark_deliver'),
                            'scripts', 'make_report.py')
        if os.path.isfile(path):
            return path
    except Exception:  # noqa: BLE001 没有 rospkg 就退回同目录
        pass
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'make_report.py')
    return local if os.path.isfile(local) else None


def rebuild_report(save_dir, title, thumb_width=320):
    script = find_report_script()
    if script is None:
        raise RuntimeError('找不到 make_report.py')
    subprocess.check_call([
        sys.executable, script,
        '--dir', save_dir,
        '--title', title,
        '--thumb-width', str(thumb_width),
    ], timeout=60)


class PhotoNode(object):
    def __init__(self):
        rospy.init_node('photo')

        self.image_topic = rospy.get_param('~image_topic', '/camera/color/image_raw')
        self.save_dir = os.path.expanduser(
            rospy.get_param('~save_dir', '~/.spark_deliver/photos'))
        self.quality = int(rospy.get_param('~jpeg_quality', 88))
        self.frame_timeout = float(rospy.get_param('~frame_timeout', 5.0))
        self.thumb_width = int(rospy.get_param('~thumb_width', 320))
        self.report_enable = bool(rospy.get_param('~report_enable', True))
        self.report_title = rospy.get_param('~report_title', '送物拍照记录')

        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.latest = None
        self.latest_time = 0.0
        self.count = 0

        if not os.path.isdir(self.save_dir):
            os.makedirs(self.save_dir)

        self.saved_pub = rospy.Publisher(SAVED_TOPIC, String, queue_size=10)
        rospy.Subscriber(self.image_topic, Image, self._image_cb, queue_size=1)
        rospy.Subscriber(EVENT_TOPIC, String, self._event_cb, queue_size=10)

        rospy.loginfo('拍照节点就绪：图像=%s 目录=%s',
                      self.image_topic, self.save_dir)

    # ---------------- 回调 ----------------
    def _image_cb(self, msg):
        with self.lock:
            self.latest = msg
            self.latest_time = time.time()

    def _event_cb(self, msg):
        try:
            event = json.loads(msg.data)
        except ValueError:
            rospy.logwarn('拍照事件格式不对：%s', msg.data)
            return

        place = str(event.get('place', '未知地点'))
        tag = str(event.get('tag', 'arrive'))
        path = self._capture(place, tag)
        if path:
            self.count += 1
            payload = {
                'place': place,
                'tag': tag,
                'file': path,
                'total': self.count,
            }
            self.saved_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    # ---------------- 抓拍 ----------------
    def _wait_frame(self):
        deadline = time.time() + self.frame_timeout
        while time.time() < deadline and not rospy.is_shutdown():
            with self.lock:
                msg = self.latest
                stamp = self.latest_time
            if msg is not None and (time.time() - stamp) < self.frame_timeout:
                return msg
            time.sleep(0.05)
        return None

    def _capture(self, place, tag):
        msg = self._wait_frame()
        if msg is None:
            rospy.logwarn('没有等到图像，%s 这张没拍成（检查相机是否启动）', place)
            return None

        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except CvBridgeError as exc:
            rospy.logerr('图像转换失败：%s', exc)
            return None

        now = time.localtime()
        day = time.strftime('%Y-%m-%d', now)
        day_dir = os.path.join(self.save_dir, day)
        if not os.path.isdir(day_dir):
            os.makedirs(day_dir)

        fname = '%s_%s.jpg' % (time.strftime('%H%M%S', now), self._safe_name(place))
        full_path = os.path.join(day_dir, fname)
        cv2.imwrite(full_path, frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])

        rel_path = os.path.join(day, fname)
        record = {
            'file': rel_path,
            'place': place,
            'tag': tag,
            'stamp': time.time(),
            'time': time.strftime('%Y-%m-%d %H:%M:%S', now),
        }
        self._append_meta(record)
        rospy.loginfo('已存照片：%s', full_path)

        if self.report_enable:
            try:
                rebuild_report(self.save_dir, self.report_title, self.thumb_width)
            except Exception as exc:  # noqa: BLE001 索引失败不影响拍照
                rospy.logwarn('生成网页索引失败：%s', exc)

        return rel_path

    def _append_meta(self, record):
        meta_path = os.path.join(self.save_dir, 'meta.jsonl')
        with open(meta_path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')

    @staticmethod
    def _safe_name(name):
        bad = '\\/:*?"<>| \t'
        cleaned = ''.join('_' if ch in bad else ch for ch in str(name)).strip('_')
        return cleaned or 'place'


def main():
    PhotoNode()
    rospy.spin()


if __name__ == '__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        pass

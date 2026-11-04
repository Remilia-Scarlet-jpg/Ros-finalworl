#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示教录点（teach_waypoints）—— 遥控到位，按空格记一个航点

不用手填坐标，也不用记数字：把车开到地方，按一下键就记下来。

用法
----
    # 终端 1：只起地图 + 定位 + 导航（不起送物节点，省得抢）
    roslaunch spark_deliver deliver.launch start_deliver:=false

    # 终端 2：示教
    rosrun spark_deliver teach_waypoints.py

    # 用你的遥控方式（手机 / SDK / 手柄）把车开到第一个点
    # 按空格 → 记一个航点，接着开去下一个，再按空格
    # 按 q 退出

没有遥控器时可以加 --teleop，用键盘开：

    rosrun spark_deliver teach_waypoints.py --teleop

按键
----
    空格 / 回车   记一个航点，自动命名 航点1、航点2……
    n            记一个航点，自己取名
    h            把当前位置设为出发点
    u            撤销上一个航点
    l            看已经记了哪些
    q            退出（会通知送物节点重载地点表，不用重启）

    --teleop 时另外还有：w 前进  s 后退  a 左转  d 右转（大写 2 倍速）

参数
----
    --prefix 工位    自动命名的前缀，默认「航点」
    --actions        到点动作，默认 deliver,photo
    --no-beep        记录时不响铃
    --teleop         自带键盘遥控
    --file           指定别的地点表
"""

from __future__ import print_function

import argparse
import math
import os
import sys
import time

import rospy
from std_srvs.srv import Trigger

LINE = '-' * 58


# ----------------------------------------------------------------- 复用地点表读写

def load_place_manager():
    """直接加载同目录的 place_manager.py，读写地点表的逻辑只写一份。"""
    import importlib.util

    candidates = []
    here = os.path.dirname(os.path.realpath(__file__))
    candidates.append(os.path.join(here, 'place_manager.py'))
    try:
        import rospkg
        candidates.append(os.path.join(
            rospkg.RosPack().get_path('spark_deliver'), 'scripts',
            'place_manager.py'))
    except Exception:  # noqa: BLE001
        pass

    for path in candidates:
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location('place_manager', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError('找不到 place_manager.py，检查功能包是否完整')


# ----------------------------------------------------------------- 位姿

class PoseReader(object):
    def __init__(self, frame='map', base='base_footprint'):
        import tf2_ros
        import tf.transformations as tft
        self._tft = tft
        self.buffer = tf2_ros.Buffer()
        self.listener = tf2_ros.TransformListener(self.buffer)
        self.frame = frame
        self.base = base
        rospy.sleep(0.5)

    def read(self, timeout=2.0):
        try:
            transform = self.buffer.lookup_transform(
                self.frame, self.base, rospy.Time(0), rospy.Duration(timeout))
        except Exception as exc:  # noqa: BLE001  tf2 异常类型随版本变
            return None, str(exc)
        translate = transform.transform.translation
        rotate = transform.transform.rotation
        yaw = self._tft.euler_from_quaternion(
            [rotate.x, rotate.y, rotate.z, rotate.w])[2]
        return (translate.x, translate.y, yaw), None


# ----------------------------------------------------------------- 键盘

class Keyboard(object):
    """单键读取：按一下就走，不用回车。不是终端时退化成按行读。"""

    def __init__(self):
        self.fd = None
        self.saved = None

    def __enter__(self):
        if sys.stdin.isatty():
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *_exc):
        if self.fd is None:
            return
        import termios
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def key(self):
        if self.fd is None:
            line = sys.stdin.readline()
            if not line:
                return 'q'
            return line.strip()[:1] or '\n'
        char = sys.stdin.read(1)
        return char if char else 'q'

    def key_wait(self, timeout=0.1):
        """最多等 timeout 秒；没按键返回 None（遥控模式要用这个轮询）。"""
        import select
        try:
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not ready:
            return None
        if self.fd is None:
            line = sys.stdin.readline()
            if not line:
                return 'q'
            return line.strip()[:1] or '\n'
        char = sys.stdin.read(1)
        return char if char else 'q'

    def drain_escape(self):
        """方向键之类的转义序列，吃掉别当成指令。"""
        import select
        while select.select([sys.stdin], [], [], 0.01)[0]:
            sys.stdin.read(1)

    def line(self, prompt):
        if self.fd is None:
            return input(prompt).strip()
        import termios
        import tty
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        try:
            return input(prompt).strip()
        finally:
            tty.setcbreak(self.fd)


# ----------------------------------------------------------------- 键盘遥控

class Teleop(object):
    """没有手机/SDK 遥控时的备用方案：w/a/s/d 点动，松手 0.8 秒自动停。"""

    SPEED = 0.15
    TURN = 0.7
    HOLD = 0.8

    def __init__(self):
        from geometry_msgs.msg import Twist
        self._twist = Twist
        self.pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
        self.last = 0.0
        self.moving = False

    def drive(self, key):
        twist = self._twist()
        factor = 2.0 if key.isupper() else 1.0
        low = key.lower()
        if low == 'w':
            twist.linear.x = self.SPEED * factor
        elif low == 's':
            twist.linear.x = -self.SPEED * factor
        elif low == 'a':
            twist.angular.z = self.TURN * factor
        elif low == 'd':
            twist.angular.z = -self.TURN * factor
        else:
            return
        self.pub.publish(twist)
        self.last = time.time()
        self.moving = True

    def tick(self):
        if self.moving and time.time() - self.last > self.HOLD:
            self.stop()

    def stop(self):
        self.pub.publish(self._twist())
        self.moving = False


# ----------------------------------------------------------------- 示教

class Teacher(object):
    def __init__(self, pm, data, path, args):
        self.pm = pm
        self.data = data
        self.path = path
        self.prefix = args.prefix
        self.actions = pm.parse_actions(args.actions)
        self.beep = not args.no_beep
        self.reader = PoseReader()
        self.count = 0

    # ---------------- 小工具 ----------------
    def _hint(self, text):
        print('  %s' % text)

    def _tone(self, ok=True):
        if not self.beep:
            return
        if ok:
            sys.stdout.write('\a')
        else:
            sys.stdout.write('\a\a')
        sys.stdout.flush()

    def _next_name(self):
        used = set(item['name'] for item in self.data['places'])
        index = 1
        while '%s%d' % (self.prefix, index) in used:
            index += 1
        return '%s%d' % (self.prefix, index)

    def _pose(self):
        pose, error = self.reader.read()
        if pose is None:
            self._tone(False)
            self._hint('取不到位姿：%s' % error)
            self._hint('确认地图、定位（amcl）和底盘都起来了')
        return pose

    def _save(self):
        self.pm.save_places(self.path, self.data)

    def _summary(self, index):
        item = self.data['places'][index]
        print('  [%d] %s   x=%.2f y=%.2f  朝向 %.0f°   （共 %d 个）'
              % (index + 1, item['name'], item['x'], item['y'],
                 math.degrees(item['yaw']) % 360.0, len(self.data['places'])))

    # ---------------- 动作 ----------------
    def mark(self, name=None):
        pose = self._pose()
        if pose is None:
            return
        x, y, yaw = pose
        if not name:
            name = self._next_name()
            entry = {'name': name, 'x': round(x, 3), 'y': round(y, 3),
                     'yaw': round(yaw, 3), 'actions': list(self.actions)}
            self.data['places'].append(entry)
            index = len(self.data['places']) - 1
        else:
            existing = self.pm.find_index(self.data, name)
            if existing is not None:
                self.data['places'][existing].update(
                    {'x': round(x, 3), 'y': round(y, 3), 'yaw': round(yaw, 3)})
                index = existing
                print('  已更新同名航点')
            else:
                self.data['places'].append(
                    {'name': name, 'x': round(x, 3), 'y': round(y, 3),
                     'yaw': round(yaw, 3), 'actions': list(self.actions)})
                index = len(self.data['places']) - 1
        self._save()
        self._summary(index)
        self._tone()

    def set_home(self):
        pose = self._pose()
        if pose is None:
            return
        x, y, yaw = pose
        self.data['home'] = {'name': '出发点', 'x': round(x, 3),
                             'y': round(y, 3), 'yaw': round(yaw, 3)}
        self._save()
        print('  出发点 = (%.2f, %.2f)  朝向 %.0f°'
              % (x, y, math.degrees(yaw) % 360.0))
        self._tone()

    def undo(self):
        if not self.data['places']:
            self._hint('还没有航点可撤销')
            self._tone(False)
            return
        item = self.data['places'].pop()
        self._save()
        print('  已撤销「%s」，还剩 %d 个' % (item['name'],
                                              len(self.data['places'])))
        self._tone()

    def show(self):
        self.pm.print_list(self.data, self.path)

    def banner(self, teleop=False):
        single_key = sys.stdin.isatty()
        print('')
        print(LINE)
        print(' 示教录点')
        print(' 地点表：%s' % self.path)
        print(LINE)
        if teleop:
            print(' 键盘遥控：w 前进  s 后退  a 左转  d 右转')
            print('           大写 2 倍速，松手 0.8 秒自动停，空格停车并记点')
            print(LINE)
        print(' 先把车开到目标位置，然后：')
        print('')
        if single_key:
            print('   [空格]  记一个航点（自动命名 %s1、%s2…）'
                  % (self.prefix, self.prefix))
        else:
            print('   [回车]  记一个航点（自动命名 %s1、%s2…）'
                  % (self.prefix, self.prefix))
            print('          （当前不是交互终端，按一下键不行，得按回车）')
        print('   n       记一个航点，自己取名')
        print('   h       把当前位置设为出发点')
        print('   u       撤销上一个航点')
        print('   l       看已记录了哪些')
        print('   q       退出')
        print(LINE)
        print(' 现在有 %d 个航点，出发点 (%.2f, %.2f)'
              % (len(self.data['places']), self.data['home']['x'],
                 self.data['home']['y']))
        print(' 等待按键…')
        print('')


def notify_reload():
    """示教完让送物节点重读地点表，省得重启。"""
    try:
        rospy.wait_for_service('/spark_deliver/reload', timeout=1.0)
        response = rospy.ServiceProxy('/spark_deliver/reload', Trigger)()
        return response.message
    except Exception:  # noqa: BLE001  没起业务节点很正常
        return None


def run(args):
    pm = load_place_manager()

    path = os.path.expanduser(args.file) if args.file \
        else pm.default_places_file()
    data = pm.load_places(path)

    rospy.init_node('teach_waypoints', anonymous=True)
    teacher = Teacher(pm, data, path, args)
    teacher.banner(teleop=args.teleop)

    with Keyboard() as keyboard:
        teleop = Teleop() if args.teleop else None
        try:
            while not rospy.is_shutdown():
                key = keyboard.key_wait(0.1)
                if key is None:
                    if teleop:
                        teleop.tick()
                    continue

                if key in ('q', 'Q', '\x03'):
                    break
                if key in (' ', '\n', '\r'):
                    if teleop:
                        teleop.stop()
                    teacher.mark()
                elif key in ('n', 'N'):
                    if teleop:
                        teleop.stop()
                    name = keyboard.line('航点名 > ')
                    if name:
                        teacher.mark(name)
                    else:
                        teacher._hint('名字为空，这次没记')
                elif teleop and key in ('w', 's', 'a', 'd',
                                        'W', 'S', 'A', 'D'):
                    teleop.drive(key)
                elif key in ('h', 'H'):
                    if teleop:
                        teleop.stop()
                    teacher.set_home()
                elif key in ('u', 'U'):
                    teacher.undo()
                elif key in ('l', 'L'):
                    teacher.show()
                    print(' 等待按键…')
                elif key == '\x1b':
                    keyboard.drain_escape()
                else:
                    continue
        finally:
            if teleop:
                teleop.stop()

    print('')
    total = len(data['places'])
    print(LINE)
    print(' 退出示教：%d 个航点，出发点 (%.2f, %.2f)'
          % (total, data['home']['x'], data['home']['y']))
    print(' 地点表：%s' % path)

    message = notify_reload()
    if message:
        print(' 送物节点：%s' % message)
        print(' 可以直接用了，不用重启')
    else:
        print(' 送物节点没在跑（或正忙）。启动/重载：')
        print('   roslaunch spark_deliver deliver.launch')
        print('   rosservice call /spark_deliver/reload "{}"')
    print(LINE)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description='示教录点：遥控到位，按键记航点')
    parser.add_argument('--prefix', default='航点', help='自动命名的前缀')
    parser.add_argument('--actions', default='deliver,photo',
                        help='到点动作，逗号分隔')
    parser.add_argument('--no-beep', action='store_true', help='记录时不响铃')
    parser.add_argument('--teleop', action='store_true',
                        help='自带键盘遥控（w/a/s/d），不用外部遥控器')
    parser.add_argument('-f', '--file', default=None, help='地点表路径')
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except rospy.ROSInterruptException:
        return 0


if __name__ == '__main__':
    raw = [item for item in sys.argv[1:] if ':=' not in item]
    try:
        sys.exit(main(raw))
    except KeyboardInterrupt:
        print('')
        sys.exit(130)

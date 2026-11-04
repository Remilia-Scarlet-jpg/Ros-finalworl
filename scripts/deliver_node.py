#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""送物调度节点（deliver_node）

流程（task=deliver，默认）：
    选地点 → 导航过去 → 到点拍照留档 → 语音提示"请取走" → 等人确认 → 返回出发点

流程（task=photo_round）：
    依次去所有带 photo 的地点拍照 → 回到出发点

对外接口
--------
订阅  /spark_deliver/command      std_msgs/String   指令，见下方说明
发布  /spark_deliver/status       std_msgs/String   状态（JSON，latched）
发布  /spark_deliver/photo_event  std_msgs/String   到点拍照事件（JSON）
发布  /voice/tts                  std_msgs/String   语音播报（沿用工作区既有约定）
服务  /spark_deliver/cancel       std_srvs/Trigger  取消当前任务
服务  /spark_deliver/confirm      std_srvs/Trigger  确认取件，立即返航
动作  move_base（客户端）

command 支持的写法：
    1  / 2 / 3      按编号选地点（编号见列表）
    前台            按名字选（支持部分匹配，如"会议"）
    list            列出地点
    confirm         确认取件
    cancel          取消任务
    photo           开始巡拍一圈
    back            回出发点

本节点只调用已有导航栈，不修改任何其他功能包。
"""

from __future__ import print_function

import json
import os
import threading
import time

import actionlib
import rospy
import tf.transformations as tft
import yaml
from geometry_msgs.msg import PoseWithCovarianceStamped
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from std_msgs.msg import String
from std_srvs.srv import Trigger, TriggerResponse

IDLE = 'idle'
NAVIGATING = 'navigating'
ARRIVED = 'arrived'
WAITING = 'waiting'
RETURNING = 'returning'
FINISHED = 'finished'
ERROR = 'error'

BUSY_STATES = (NAVIGATING, ARRIVED, WAITING, RETURNING)


class DeliverNode(object):
    def __init__(self):
        rospy.init_node('deliver')

        self.lock = threading.RLock()
        self.preempt = threading.Event()      # 取消标志
        self.pickup_event = threading.Event()  # 取件确认
        self.pending = None                    # 待执行任务

        # ---------------- 参数 ----------------
        self.places_file = os.path.expanduser(rospy.get_param('~places_file', ''))
        self.task_type = rospy.get_param('~task', 'deliver')
        self.goal_timeout = float(rospy.get_param('~goal_timeout', 120.0))
        self.max_retries = int(rospy.get_param('~max_retries', 1))
        self.wait_pickup_timeout = float(
            rospy.get_param('~wait_pickup_timeout', 60.0))
        self.return_home = bool(rospy.get_param('~return_home', True))
        self.photo_on_arrive = bool(rospy.get_param('~photo_on_arrive', True))
        self.arrive_pause = float(rospy.get_param('~arrive_pause', 1.5))
        self.tts_enable = bool(rospy.get_param('~tts_enable', True))
        self.tts_topic = rospy.get_param('~tts_topic', '/voice/tts')
        self.frame_id = rospy.get_param('~frame_id', 'map')
        self.server_wait = float(rospy.get_param('~server_wait', 30.0))
        self.publish_initial_pose = bool(
            rospy.get_param('~publish_initial_pose', True))
        self.initial_pose_delay = float(
            rospy.get_param('~initial_pose_delay', 6.0))

        # ---------------- 地点表 ----------------
        self.home = {'name': '出发点', 'x': 0.0, 'y': 0.0, 'yaw': 0.0,
                     'actions': ['deliver', 'photo']}
        self.places = []
        self._load_places()

        # ---------------- 状态 ----------------
        self.state = IDLE
        self.text = '待命'
        self.place = ''

        # ---------------- 通信 ----------------
        self.status_pub = rospy.Publisher(
            '/spark_deliver/status', String, queue_size=10, latch=True)
        self.photo_pub = rospy.Publisher(
            '/spark_deliver/photo_event', String, queue_size=10)
        self.tts_pub = rospy.Publisher(self.tts_topic, String, queue_size=10)

        rospy.Subscriber('/spark_deliver/command', String, self._cmd_cb, queue_size=10)
        rospy.Service('/spark_deliver/cancel', Trigger, self._cancel_srv)
        rospy.Service('/spark_deliver/confirm', Trigger, self._confirm_srv)
        rospy.Service('/spark_deliver/reload', Trigger, self._reload_srv)

        self.client = actionlib.SimpleActionClient('move_base', MoveBaseAction)

        # ---------------- 后台线程 ----------------
        self.worker = threading.Thread(target=self._worker)
        self.worker.daemon = True
        self.worker.start()

        if self.publish_initial_pose:
            publisher = threading.Thread(target=self._publish_initial_pose)
            publisher.daemon = True
            publisher.start()

        # 巡拍模式默认开机就跑，送物模式要等人选地点
        auto_start = bool(rospy.get_param('~auto_start',
                                          self.task_type == 'photo_round'))
        if auto_start and self.task_type == 'photo_round':
            with self.lock:
                self.pending = {'type': 'photo_round'}
            rospy.loginfo('巡拍模式：开机自动开始')

        self.timer = rospy.Timer(rospy.Duration(1.0), lambda _: self._publish_status())

        rospy.loginfo('送物节点就绪：%d 个地点，任务类型=%s',
                      len(self.places), self.task_type)
        self._publish_status()

    # ==================== 初始化辅助 ====================
    def _load_places(self):
        if not self.places_file:
            rospy.logfatal('没有指定 places_file 参数')
            raise RuntimeError('places_file 未设置')
        if not os.path.isfile(self.places_file):
            rospy.logfatal('地点表不存在：%s', self.places_file)
            raise RuntimeError('地点表不存在：%s' % self.places_file)

        with open(self.places_file, 'r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}

        # 可能被 reload 服务重复调用，先清空再读，别把点追加两遍
        self.home = {'name': '出发点', 'x': 0.0, 'y': 0.0, 'yaw': 0.0,
                     'actions': ['deliver', 'photo']}
        self.places = []

        home = data.get('home')
        if isinstance(home, dict):
            self.home = self._normalize(home, '出发点')

        for item in data.get('places') or []:
            if isinstance(item, dict):
                self.places.append(self._normalize(item, '地点%d' % (len(self.places) + 1)))

        if not self.places:
            rospy.logwarn('地点表里还没有地点，先标一个：')
            rospy.logwarn('  rosrun spark_deliver place_manager.py add 前台 '
                          '--x 1.2 --y 0.5 --yaw 90')
            rospy.logwarn('  或者 rosrun spark_deliver place_manager.py 进交互菜单')
        rospy.loginfo('已载入地点：%s',
                      '、'.join('%d.%s' % (i + 1, p['name'])
                                for i, p in enumerate(self.places)))

    @staticmethod
    def _normalize(raw, default_name):
        actions = raw.get('actions', ['deliver', 'photo'])
        if isinstance(actions, str):
            actions = [a.strip() for a in actions.split(',') if a.strip()]
        return {
            'name': str(raw.get('name', default_name)).strip() or default_name,
            'x': float(raw.get('x', 0.0)),
            'y': float(raw.get('y', 0.0)),
            'yaw': float(raw.get('yaw', 0.0)),
            'actions': list(actions),
        }

    # ==================== 状态与播报 ====================
    def _set_state(self, state, text, place=None):
        with self.lock:
            self.state = state
            self.text = text
            if place is not None:
                self.place = place
        rospy.loginfo('[%s] %s', state, text)
        self._publish_status()

    def _publish_status(self):
        payload = {
            'state': self.state,
            'text': self.text,
            'place': self.place,
            'busy': self.state in BUSY_STATES,
            'places': [p['name'] for p in self.places],
        }
        self.status_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _publish_initial_pose(self):
        """把出发点当作初始位姿发给 AMCL，省得每次手动点 2D Pose Estimate。"""
        rospy.sleep(self.initial_pose_delay)
        if rospy.is_shutdown():
            return

        pub = rospy.Publisher('/initialpose', PoseWithCovarianceStamped, queue_size=1)
        rospy.sleep(0.5)

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = self.frame_id
        quat = tft.quaternion_from_euler(0.0, 0.0, self.home['yaw'])
        msg.pose.pose.position.x = self.home['x']
        msg.pose.pose.position.y = self.home['y']
        msg.pose.pose.orientation.x = quat[0]
        msg.pose.pose.orientation.y = quat[1]
        msg.pose.pose.orientation.z = quat[2]
        msg.pose.pose.orientation.w = quat[3]
        covariance = [0.0] * 36
        covariance[0] = 0.25
        covariance[7] = 0.25
        covariance[35] = 0.068
        msg.pose.covariance = covariance

        for _ in range(3):
            if rospy.is_shutdown():
                return
            msg.header.stamp = rospy.Time.now()
            pub.publish(msg)
            rospy.loginfo('已把出发点 (%.2f, %.2f) 设为初始位姿',
                          self.home['x'], self.home['y'])
            rospy.sleep(1.0)

    def _say(self, text):
        rospy.loginfo('[播报] %s', text)
        if self.tts_enable:
            self.tts_pub.publish(String(data=text))

    # ==================== 指令入口 ====================
    def _cmd_cb(self, msg):
        text = (msg.data or '').strip()
        if not text:
            return
        low = text.lower()

        if low in ('list', 'ls', '列表'):
            self._say(self._place_speech())
            return
        if low in ('reload', '重载', '重新加载'):
            self._say(self._reload_srv(None).message)
            return
        if low in ('cancel', 'stop', '取消'):
            self._do_cancel()
            self._say('任务已取消')
            return
        if low in ('confirm', 'ok', '取走', '已取走'):
            self.pickup_event.set()
            rospy.loginfo('收到取件确认')
            return
        if low in ('photo', '拍照', '巡拍'):
            self._start_job({'type': 'photo_round'})
            return
        if low in ('back', 'home', '返回', '回家'):
            self._start_job({'type': 'home'})
            return

        place = self._match_place(text)
        if place is None:
            self._say('没有找到地点%s' % text)
            return
        self._start_job({'type': 'deliver', 'place': place})

    def _match_place(self, text):
        key = text.strip()
        if key.isdigit():
            index = int(key) - 1
            if 0 <= index < len(self.places):
                return self.places[index]
            return None
        for place in self.places:
            if key == place['name']:
                return place
        for place in self.places:
            if key in place['name'] or place['name'] in key:
                return place
        return None

    def _place_speech(self):
        if not self.places:
            return '还没有配置地点'
        parts = ['%d号%s' % (i + 1, p['name']) for i, p in enumerate(self.places)]
        return '可以去：' + '，'.join(parts)

    def _start_job(self, job):
        with self.lock:
            if self.state in BUSY_STATES or self.pending is not None:
                rospy.logwarn('正在执行任务，忽略新指令')
                self._say('正在忙，请稍等')
                return
            self.pending = job
        rospy.loginfo('收到任务：%s', job.get('type'))

    def _do_cancel(self):
        self.preempt.set()
        self.pickup_event.clear()
        with self.lock:
            self.pending = None
        if self.client.get_state() in (actionlib.GoalStatus.ACTIVE,
                                       actionlib.GoalStatus.PENDING):
            self.client.cancel_goal()
        self._set_state(IDLE, '已取消')

    def _cancel_srv(self, _req):
        self._do_cancel()
        return TriggerResponse(success=True, message='已取消')

    def _confirm_srv(self, _req):
        self.pickup_event.set()
        return TriggerResponse(success=True, message='已确认取件')

    def _reload_srv(self, _req):
        """示教完不用重启节点，调一下这个就把新航点读进来。"""
        with self.lock:
            busy = self.state in (NAVIGATING, ARRIVED, WAITING, RETURNING)
        if busy:
            return TriggerResponse(
                success=False,
                message='正在执行任务（%s），先按 x 取消再重载' % self.state)
        try:
            self._load_places()
        except Exception as exc:  # noqa: BLE001
            return TriggerResponse(success=False, message='重载失败：%s' % exc)
        self._publish_status()
        return TriggerResponse(
            success=True,
            message='已重新载入 %d 个地点' % len(self.places))

    # ==================== 任务线程 ====================
    def _worker(self):
        rate_sleep = 0.1
        while not rospy.is_shutdown():
            with self.lock:
                job = self.pending
                self.pending = None

            if job is None:
                time.sleep(rate_sleep)
                continue

            self.preempt.clear()
            try:
                kind = job.get('type')
                if kind == 'photo_round':
                    self._run_photo_round()
                elif kind == 'home':
                    self._run_home()
                else:
                    self._run_deliver(job.get('place'))
            except Exception as exc:  # noqa: BLE001 保证线程不退出
                rospy.logerr('任务异常：%s', exc)
                self._set_state(ERROR, '任务异常：%s' % exc)
            finally:
                self.preempt.clear()
                self.pickup_event.clear()

    def _run_deliver(self, place):
        if place is None:
            self._set_state(IDLE, '没有指定地点')
            return

        name = place['name']
        self._say('出发，送往%s' % name)
        self._set_state(NAVIGATING, '前往%s' % name, name)

        if not self._goto(place):
            if self.preempt.is_set():
                self._set_state(IDLE, '已取消', name)
            else:
                self._set_state(ERROR, '无法到达%s' % name, name)
                self._say('抱歉，没能到达%s' % name)
            return

        self._set_state(ARRIVED, '已到达%s' % name, name)
        if self.photo_on_arrive and 'photo' in place['actions']:
            self._trigger_photo(place, 'arrive')
        time.sleep(self.arrive_pause)

        self._set_state(WAITING, '等待取件', name)
        self._say('%s，东西送到了，请取走' % name)

        confirmed = self._wait_pickup()
        if self.preempt.is_set():
            self._set_state(IDLE, '已取消', name)
            return

        if confirmed:
            self._say('好的，我回去了')
        else:
            self._say('没人来取，我先回去了')

        if not self.return_home:
            self._set_state(FINISHED, '任务结束', name)
            return

        self._set_state(RETURNING, '返回出发点', self.home['name'])
        if self._goto(self.home):
            self._set_state(FINISHED, '已回到出发点', self.home['name'])
            self._say('已回到出发点')
        else:
            self._set_state(ERROR, '返回出发点失败')

    def _run_photo_round(self):
        targets = [p for p in self.places if 'photo' in p['actions']]
        if not targets:
            self._set_state(ERROR, '没有带 photo 标记的地点')
            self._say('没有配置拍照点')
            return

        self._say('开始巡拍，共%d个点' % len(targets))
        done = 0
        for index, place in enumerate(targets):
            if self.preempt.is_set():
                break
            self._set_state(NAVIGATING,
                            '前往%s（%d/%d）' % (place['name'], index + 1, len(targets)),
                            place['name'])
            if not self._goto(place):
                rospy.logwarn('跳过拍照点：%s', place['name'])
                continue
            self._set_state(ARRIVED, '拍摄%s' % place['name'], place['name'])
            self._trigger_photo(place, 'round')
            done += 1
            time.sleep(self.arrive_pause)

        if self.preempt.is_set():
            self._set_state(IDLE, '巡拍已取消')
            return

        if self.return_home:
            self._set_state(RETURNING, '返回出发点', self.home['name'])
            self._goto(self.home)

        self._set_state(FINISHED, '巡拍结束，共%d张' % done)
        self._say('巡拍结束，一共拍了%d张' % done)

    def _run_home(self):
        self._set_state(RETURNING, '返回出发点', self.home['name'])
        if self._goto(self.home):
            self._set_state(FINISHED, '已回到出发点', self.home['name'])
        else:
            self._set_state(ERROR, '返回出发点失败')

    # ==================== 导航 ====================
    def _make_goal(self, place):
        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = self.frame_id
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose.position.x = place['x']
        goal.target_pose.pose.position.y = place['y']
        quat = tft.quaternion_from_euler(0.0, 0.0, place['yaw'])
        goal.target_pose.pose.orientation.x = quat[0]
        goal.target_pose.pose.orientation.y = quat[1]
        goal.target_pose.pose.orientation.z = quat[2]
        goal.target_pose.pose.orientation.w = quat[3]
        return goal

    def _wait_server(self):
        rospy.loginfo('等待 move_base 上线...')
        if not self.client.wait_for_server(rospy.Duration(self.server_wait)):
            rospy.logerr('move_base 没上线，导航不可用')
            self._say('导航没准备好')
            return False
        return True

    def _goto(self, place):
        if not self._wait_server():
            return False

        for attempt in range(self.max_retries + 1):
            if self.preempt.is_set():
                return False

            rospy.loginfo('导航目标：%s (%.2f, %.2f, %.2f) 第%d次',
                          place['name'], place['x'], place['y'], place['yaw'],
                          attempt + 1)
            self.client.send_goal(self._make_goal(place))

            if self._wait_goal():
                if self.client.get_state() == actionlib.GoalStatus.SUCCEEDED:
                    return True
                if self.preempt.is_set():
                    return False
                rospy.logwarn('到达 %s 失败，state=%s', place['name'],
                              self.client.get_state())
            else:
                if self.preempt.is_set():
                    self.client.cancel_goal()
                    self.client.wait_for_result(rospy.Duration(1.0))
                    return False
                rospy.logwarn('前往 %s 超时（%.0fs）', place['name'], self.goal_timeout)
                self.client.cancel_goal()
                self.client.wait_for_result(rospy.Duration(1.0))
        return False

    def _wait_goal(self):
        deadline = time.time() + self.goal_timeout
        while not rospy.is_shutdown():
            if self.preempt.is_set():
                return False
            if self.client.wait_for_result(rospy.Duration(0.2)):
                return True
            if time.time() > deadline:
                return False
        return False

    # ==================== 取件等待 ====================
    def _wait_pickup(self):
        self.pickup_event.clear()
        deadline = None
        if self.wait_pickup_timeout > 0:
            deadline = time.time() + self.wait_pickup_timeout

        while not rospy.is_shutdown():
            if self.preempt.is_set():
                return False
            if self.pickup_event.wait(0.2):
                return True
            if deadline is not None and time.time() > deadline:
                rospy.loginfo('等待取件超时')
                return False
        return False

    # ==================== 拍照 ====================
    def _trigger_photo(self, place, tag):
        payload = {
            'place': place['name'],
            'tag': tag,
            'task': self.task_type,
            'stamp': rospy.Time.now().to_sec(),
        }
        self.photo_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))
        rospy.loginfo('触发拍照：%s (%s)', place['name'], tag)
        time.sleep(0.6)


def main():
    node = DeliverNode()
    rospy.on_shutdown(node._do_cancel)
    rospy.spin()


if __name__ == '__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        pass

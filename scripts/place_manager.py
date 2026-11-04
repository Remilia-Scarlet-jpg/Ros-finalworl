#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地点管理（place_manager）—— 手动标定导航点，保存 / 修改 / 删除

纯配置文件工具，不开 ROS 也能用：直接读写 config/places.yaml。
角度在命令行和菜单里都按「度」输入输出，写进文件时自动换成弧度。

用法
----
交互菜单（推荐，不用记参数）：
    rosrun spark_deliver place_manager.py

看已标好的点：
    rosrun spark_deliver place_manager.py list

手动加一个点（朝向 0 = 朝 map 的 +x，90 = 朝 +y）：
    rosrun spark_deliver place_manager.py add 前台 --x 1.20 --y 0.50 --yaw 90

改坐标（只写要改的字段，其余保持不变）：
    rosrun spark_deliver place_manager.py set 前台 --x 1.35 --yaw 45

删掉（名字或编号都行）：
    rosrun spark_deliver place_manager.py del 前台
    rosrun spark_deliver place_manager.py del 2

设出发点：
    rosrun spark_deliver place_manager.py home --x 0 --y 0 --yaw 0

清空全部地点（保留出发点）：
    rosrun spark_deliver place_manager.py clear

不想手抄数字时，把机器人开到位置再记（这两条需要 ROS 在跑）：
    rosrun spark_deliver place_manager.py pose          # 只打印当前位置
    rosrun spark_deliver place_manager.py here 前台      # 用当前位置建点

每次写入前会把原文件备份成 places.yaml.bak。
"""

from __future__ import print_function

import argparse
import math
import os
import shutil
import sys

import yaml

LINE = '-' * 58
DEFAULT_ACTIONS = ['deliver', 'photo']

HEADER = """\
# ============================================================
# 地点表 —— 送物小车的导航点（手动标定）
#
# 用 place_manager.py 管理，也可以手工编辑：
#   rosrun spark_deliver place_manager.py          # 交互菜单：增 / 删 / 改 / 查
#   rosrun spark_deliver place_manager.py list     # 只看
#
# 坐标从哪来：
#   1) 机器人开到位后，用 RViz 看 /amcl_pose 的 x y，抄下来
#   2) 或者直接 place_manager.py here 地点名，自动读当前位姿
#
# 字段：
#   x, y     地图坐标系 map 下的位置，单位米
#   yaw      到达后的朝向，单位弧度（0 = 朝 +x，1.571 = 朝 +y，3.142 = 朝 -x）
#            place_manager 命令行里用「度」，写文件时自动换弧度
#   actions  到点做什么：deliver = 可送物，photo = 到点拍照
# ============================================================
"""


# ----------------------------------------------------------------- 文件位置

def default_places_file():
    """优先用包里的 config/places.yaml，和 launch 的默认值保持一致。"""
    env = os.environ.get('SPARK_DELIVER_PLACES')
    if env:
        return os.path.expanduser(env)

    try:
        import rospkg
        pack = rospkg.RosPack().get_path('spark_deliver')
        candidate = os.path.join(pack, 'config', 'places.yaml')
        if os.path.isdir(os.path.dirname(candidate)):
            return candidate
    except Exception:  # noqa: BLE001  没装 rospkg 或没 source 环境
        pass

    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, 'config',
                                         'places.yaml'))


# ----------------------------------------------------------------- 读写 YAML

def load_places(path):
    data = {}
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        data = {}

    home = data.get('home')
    if not isinstance(home, dict):
        home = {}
    data['home'] = {
        'name': str(home.get('name') or '出发点'),
        'x': _to_float(home.get('x')),
        'y': _to_float(home.get('y')),
        'yaw': _to_float(home.get('yaw')),
    }

    places = []
    for item in data.get('places') or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        actions = item.get('actions') or []
        if isinstance(actions, str):
            actions = [part.strip() for part in actions.split(',')]
        places.append({
            'name': name,
            'x': _to_float(item.get('x')),
            'y': _to_float(item.get('y')),
            'yaw': _to_float(item.get('yaw')),
            'actions': [str(a).strip() for a in actions if str(a).strip()],
        })
    data['places'] = places

    frame = data.get('frame_id')
    data['frame_id'] = str(frame).strip() if frame else 'map'
    return data


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _num(value):
    """1.2 -> '1.2'，0 -> '0.0'，去掉多余的 0。"""
    text = '%.3f' % float(value)
    text = text.rstrip('0').rstrip('.')
    if text in ('', '-0'):
        text = '0'
    if '.' not in text:
        text += '.0'
    return text


def _text(value):
    """YAML 标量：能裸写就裸写，带特殊字符才加引号。"""
    text = str(value)
    bare = bool(text) and text.strip() == text
    if bare:
        for char in text:
            if char.isalnum() or char in '_-.':
                continue
            if char in '()（）':
                continue
            bare = False
            break
    if bare:
        return text
    return "'" + text.replace("'", "''") + "'"


def dump_places(data):
    lines = [HEADER.rstrip('\n'), '']
    lines.append('frame_id: %s' % _text(data.get('frame_id', 'map')))
    lines.append('')
    lines.append('# 出发点：每次送完东西回到这里')
    lines.append('home:')
    home = data['home']
    lines.append('  name: %s' % _text(home['name']))
    lines.append('  x: %s' % _num(home['x']))
    lines.append('  y: %s' % _num(home['y']))
    lines.append('  yaw: %s' % _num(home['yaw']))
    lines.append('')

    places = data.get('places') or []
    if not places:
        lines.append('# 还没有地点，用 place_manager.py 添加')
        lines.append('places: []')
    else:
        lines.append('places:')
        for item in places:
            lines.append('  - name: %s' % _text(item['name']))
            lines.append('    x: %s' % _num(item['x']))
            lines.append('    y: %s' % _num(item['y']))
            lines.append('    yaw: %s' % _num(item['yaw']))
            actions = item.get('actions') or []
            lines.append('    actions: [%s]' % ', '.join(actions))
            lines.append('')
        if lines[-1] == '':
            lines.pop()
    return '\n'.join(lines) + '\n'


def save_places(path, data):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    if os.path.isfile(path):
        try:
            shutil.copy2(path, path + '.bak')
        except OSError as exc:
            print('  （备份失败，继续保存：%s）' % exc)

    text = dump_places(data)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        handle.write(text)
    try:
        os.replace(temp, path)
    except OSError:
        # 有些文件系统（overlay、挂载点、网络盘）不让 rename 覆盖已有文件，
        # 退化成直接写，虽然不原子但能用。
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        try:
            os.remove(temp)
        except OSError:
            pass


# ----------------------------------------------------------------- 展示

def describe(item, with_actions=True):
    text = 'x=%s  y=%s  朝向 %s°（%s rad）' % (
        '%.2f' % item['x'], '%.2f' % item['y'],
        '%.1f' % (math.degrees(item['yaw']) % 360.0), '%.3f' % item['yaw'])
    if with_actions:
        text += '  动作 %s' % (','.join(item.get('actions') or []) or '-')
    return text


def print_list(data, path):
    print('')
    print(LINE)
    print('地点表：%s' % path)
    places = data.get('places') or []
    print('坐标系：%s    共 %d 个地点' % (data.get('frame_id', 'map'), len(places)))
    print(LINE)
    print('  出发点  %s' % describe(data['home'], with_actions=False))
    if not places:
        print('')
        print('  （还没有地点，用本工具的“新增”加一个）')
        print(LINE)
        return
    print('')
    for index, item in enumerate(places, start=1):
        print('  %d. %s' % (index, item['name']))
        print('     %s' % describe(item))
    print(LINE)


# ----------------------------------------------------------------- 查找

def find_index(data, key):
    """按编号（从 1 开始）或名字找下标，找不到返回 None。"""
    places = data.get('places') or []
    text = str(key).strip()
    if not text:
        return None
    if text.isdigit():
        index = int(text) - 1
        return index if 0 <= index < len(places) else None
    for index, item in enumerate(places):
        if item['name'] == text:
            return index
    for index, item in enumerate(places):
        if item['name'].lower() == text.lower():
            return index
    return None


def parse_actions(text):
    parts = [part.strip() for part in str(text).replace('，', ',').split(',')]
    result = [part for part in parts if part]
    return result or list(DEFAULT_ACTIONS)


# ----------------------------------------------------------------- 子命令

def cmd_list(args, data, path):
    print_list(data, path)
    return 0


def cmd_add(args, data, path):
    name = args.name.strip()
    if not name:
        print('  地点名不能为空')
        return 1

    existing = find_index(data, name)
    if existing is not None and data['places'][existing]['name'] == name:
        if not args.force:
            print('  已经有叫「%s」的地点了。要改坐标用 set，要覆盖加 --force' % name)
            return 1
        data['places'].pop(existing)

    yaw = args.yaw if args.rad else math.radians(args.yaw)
    data['places'].append({
        'name': name,
        'x': float(args.x),
        'y': float(args.y),
        'yaw': float(yaw),
        'actions': parse_actions(args.actions),
    })
    save_places(path, data)
    print('  已保存「%s」：%s' % (name, describe(data['places'][-1])))
    print('  文件：%s' % path)
    return 0


def cmd_set(args, data, path):
    index = find_index(data, args.name)
    if index is None:
        print('  没找到「%s」，用 list 看看有哪些' % args.name)
        return 1

    item = data['places'][index]
    old_name = item['name']
    changed = []
    if args.x is not None:
        item['x'] = float(args.x)
        changed.append('x')
    if args.y is not None:
        item['y'] = float(args.y)
        changed.append('y')
    if args.yaw is not None:
        item['yaw'] = float(args.yaw) if args.rad else math.radians(args.yaw)
        changed.append('yaw')
    if args.actions:
        item['actions'] = parse_actions(args.actions)
        changed.append('actions')
    if args.rename:
        new_name = args.rename.strip()
        if not new_name:
            print('  新名字不能为空')
            return 1
        clash = find_index(data, new_name)
        if clash is not None and clash != index:
            print('  已经有一个叫「%s」的地点了' % new_name)
            return 1
        item['name'] = new_name
        changed.append('name')

    if not changed:
        print('  没有要改的字段。可用：--x --y --yaw --actions --rename')
        return 1

    save_places(path, data)
    if item['name'] != old_name:
        print('  已把「%s」改名为「%s」' % (old_name, item['name']))
    rest = [name for name in changed if name != 'name']
    if rest:
        print('  已修改「%s」的 %s' % (item['name'], '、'.join(rest)))
    print('  %s' % describe(item))
    return 0


def cmd_del(args, data, path):
    index = find_index(data, args.name)
    if index is None:
        print('  没找到「%s」，用 list 看看有哪些' % args.name)
        return 1

    item = data['places'][index]
    if not args.yes:
        answer = _ask('  确认删除「%s」？(y/N) > ' % item['name'])
        if answer.lower() not in ('y', 'yes', '是'):
            print('  已取消')
            return 1

    data['places'].pop(index)
    save_places(path, data)
    print('  已删除「%s」，还剩 %d 个地点' % (item['name'], len(data['places'])))
    return 0


def cmd_home(args, data, path):
    home = dict(data['home'])
    if args.x is not None:
        home['x'] = float(args.x)
    if args.y is not None:
        home['y'] = float(args.y)
    if args.yaw is not None:
        home['yaw'] = float(args.yaw) if args.rad else math.radians(args.yaw)
    data['home'] = home
    save_places(path, data)
    print('  出发点已设为：%s' % describe(home))
    return 0


def cmd_clear(args, data, path):
    places = data.get('places') or []
    if not places:
        print('  地点表本来就是空的')
        return 0
    if not args.yes:
        names = '、'.join(item['name'] for item in places)
        print('  将要删除 %d 个地点：%s' % (len(places), names))
        answer = _ask('  确认清空？出发点会保留 (y/N) > ')
        if answer.lower() not in ('y', 'yes', '是'):
            print('  已取消')
            return 1
    data['places'] = []
    save_places(path, data)
    print('  已清空 %d 个地点，出发点保留在原位' % len(places))
    return 0


def cmd_pose(args, data, path):
    pose = _read_pose()
    if pose is None:
        return 1
    x, y, yaw = pose
    print('')
    print('  当前位置  x=%.3f  y=%.3f  朝向 %.1f°（%.3f rad）'
          % (x, y, math.degrees(yaw) % 360.0, yaw))
    return 0


def cmd_here(args, data, path):
    pose = _read_pose()
    if pose is None:
        return 1
    name = args.name.strip()
    if not name:
        print('  地点名不能为空')
        return 1

    existing = find_index(data, name)
    if existing is not None and data['places'][existing]['name'] == name:
        if not args.force:
            print('  已经有叫「%s」的地点了，要覆盖加 --force' % name)
            return 1
        data['places'].pop(existing)

    x, y, yaw = pose
    data['places'].append({
        'name': name,
        'x': round(x, 3),
        'y': round(y, 3),
        'yaw': round(yaw, 3),
        'actions': parse_actions(args.actions),
    })
    save_places(path, data)
    print('  已保存「%s」：%s' % (name, describe(data['places'][-1])))
    return 0


# ----------------------------------------------------------------- 当前位姿

def _read_pose(timeout=3.0, target='base_footprint', source='map'):
    """从 TF 读当前位姿。只有 pose / here 两个子命令会走到这里。"""
    try:
        import rospy
        import tf2_ros
        import tf.transformations as tft
    except ImportError as exc:
        print('  %s' % exc)
        print('  读当前位姿需要 ROS 环境，先 source /opt/ros/noetic/setup.bash')
        return None

    try:
        import rosgraph
        if not rosgraph.is_master_online():
            print('  连不上 ROS master。这条命令要先把 roscore 或 roslaunch 跑起来')
            print('  只想改坐标的话不用开 ROS，用 add / set / del / list 就行')
            return None
    except ImportError:
        pass

    try:
        if not rospy.core.is_initialized():
            rospy.init_node('place_manager', anonymous=True)
    except Exception as exc:  # noqa: BLE001  rospy 初始化失败的花样很多
        print('  ROS 节点初始化失败：%s' % exc)
        return None

    buffer = tf2_ros.Buffer()
    tf2_ros.TransformListener(buffer)
    try:
        transform = buffer.lookup_transform(source, target, rospy.Time(0),
                                            rospy.Duration(timeout))
    except Exception as exc:  # noqa: BLE001  tf2 异常类型随版本变
        print('  拿不到 %s -> %s 的变换：%s' % (source, target, exc))
        print('  确认导航和定位已经跑起来（map 坐标系里要能看到机器人）')
        return None

    translation = transform.transform.translation
    rotation = transform.transform.rotation
    yaw = tft.euler_from_quaternion(
        [rotation.x, rotation.y, rotation.z, rotation.w])[2]
    return translation.x, translation.y, yaw


# ----------------------------------------------------------------- 交互菜单

def _ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print('')
        raise SystemExit(0)


def _ask_float(prompt, default=None, allow_blank=False):
    while True:
        raw = _ask(prompt)
        if not raw:
            if default is not None:
                return float(default)
            if allow_blank:
                return None
            print('    这里要填数字')
            continue
        try:
            return float(raw)
        except ValueError:
            print('    这不是数字：%s' % raw)


def _ask_name(data, exclude=None):
    while True:
        name = _ask('  地点名 > ')
        if not name:
            print('    名字不能为空')
            continue
        index = find_index(data, name)
        if index is not None and index != exclude:
            print('    已经有叫「%s」的地点了，换个名字' % name)
            continue
        return name


def menu_add(data, path):
    print('')
    print('  新增地点：直接填数字，朝向用度（0 = 朝 +x，90 = 朝 +y）')
    name = _ask_name(data)
    x = _ask_float('  x（米） > ')
    y = _ask_float('  y（米） > ')
    yaw_deg = _ask_float('  朝向（度）[0] > ', default=0.0)
    actions = _ask('  到点动作 [%s] > ' % ','.join(DEFAULT_ACTIONS)) or \
        ','.join(DEFAULT_ACTIONS)

    data['places'].append({
        'name': name,
        'x': x,
        'y': y,
        'yaw': math.radians(yaw_deg),
        'actions': parse_actions(actions),
    })
    save_places(path, data)
    print('  已保存「%s」' % name)


def menu_set(data, path):
    if not data['places']:
        print('  还没有地点可改')
        return
    print_list(data, path)
    index = find_index(data, _ask('  改哪一个（编号或名字） > '))
    if index is None:
        print('  没找到')
        return

    item = data['places'][index]
    print('  回车表示保持不变')
    x = _ask_float('  x（米）[%s] > ' % ('%.2f' % item['x']), allow_blank=True)
    y = _ask_float('  y（米）[%s] > ' % ('%.2f' % item['y']), allow_blank=True)
    yaw_deg = _ask_float('  朝向（度）[%s] > '
                         % ('%.1f' % (math.degrees(item['yaw']) % 360.0)),
                         allow_blank=True)
    actions = _ask('  动作 [%s] > ' % ','.join(item.get('actions') or []))
    name = _ask('  改名（不填就用「%s」） > ' % item['name'])

    if x is not None:
        item['x'] = x
    if y is not None:
        item['y'] = y
    if yaw_deg is not None:
        item['yaw'] = math.radians(yaw_deg)
    if actions:
        item['actions'] = parse_actions(actions)
    if name and name != item['name']:
        clash = find_index(data, name)
        if clash is not None and clash != index:
            print('  已经有叫「%s」的地点了，这次不改名' % name)
        else:
            item['name'] = name
    save_places(path, data)
    print('  已更新「%s」' % item['name'])


def menu_del(data, path):
    if not data['places']:
        print('  还没有地点可删')
        return
    print_list(data, path)
    index = find_index(data, _ask('  删哪一个（编号或名字） > '))
    if index is None:
        print('  没找到')
        return
    item = data['places'][index]
    if _ask('  确认删除「%s」？(y/N) > ' % item['name']).lower() not in \
            ('y', 'yes', '是'):
        print('  已取消')
        return
    data['places'].pop(index)
    save_places(path, data)
    print('  已删除，还剩 %d 个地点' % len(data['places']))


def menu_home(data, path):
    home = data['home']
    print('')
    print('  设置出发点，回车保持不变')
    x = _ask_float('  x（米）[%.2f] > ' % home['x'], allow_blank=True)
    y = _ask_float('  y（米）[%.2f] > ' % home['y'], allow_blank=True)
    yaw_deg = _ask_float('  朝向（度）[%.1f] > '
                         % (math.degrees(home['yaw']) % 360.0),
                         allow_blank=True)
    if x is not None:
        home['x'] = x
    if y is not None:
        home['y'] = y
    if yaw_deg is not None:
        home['yaw'] = math.radians(yaw_deg)
    save_places(path, data)
    print('  出发点已更新')


def menu_clear(data, path):
    args = argparse.Namespace(yes=False)
    cmd_clear(args, data, path)


def interactive(data, path):
    while True:
        places = data.get('places') or []
        print('')
        print(LINE)
        print(' 地点管理     %d 个地点' % len(places))
        print(' 文件：%s' % path)
        print(LINE)
        print('  1. 列出全部        2. 新增地点')
        print('  3. 修改地点        4. 删除地点')
        print('  5. 设置出发点      6. 清空全部')
        print('  q. 退出')
        print(LINE)
        choice = _ask('请选择 > ').lower()

        if choice in ('q', 'quit', 'exit'):
            print('')
            break
        if choice in ('1', 'l', 'list'):
            print_list(data, path)
        elif choice in ('2', 'a', 'add'):
            menu_add(data, path)
        elif choice in ('3', 's', 'set'):
            menu_set(data, path)
        elif choice in ('4', 'd', 'del', 'delete'):
            menu_del(data, path)
        elif choice in ('5', 'h', 'home'):
            menu_home(data, path)
        elif choice in ('6', 'c', 'clear'):
            menu_clear(data, path)
        elif choice == '':
            continue
        else:
            print('  看不懂：%s' % choice)
        data = load_places(path)


# ----------------------------------------------------------------- 入口

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('-f', '--file', default=argparse.SUPPRESS,
                        help='地点表路径')

    parser = argparse.ArgumentParser(
        prog='place_manager.py',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='地点管理：手动标定导航点，支持保存 / 修改 / 删除',
        epilog='不带子命令直接运行会进交互菜单。\n'
               '角度一律用「度」，写进文件时自动换成弧度。')
    parser.add_argument('-f', '--file', default=None,
                        help='地点表路径，默认用包里的 config/places.yaml')

    sub = parser.add_subparsers(dest='action')

    sub.add_parser('list', parents=[common], help='列出全部地点')

    add = sub.add_parser('add', parents=[common], help='新增地点')
    add.add_argument('name', help='地点名')
    add.add_argument('--x', type=float, required=True, help='x，单位米')
    add.add_argument('--y', type=float, required=True, help='y，单位米')
    add.add_argument('--yaw', type=float, default=0.0,
                     help='朝向，默认单位度（0 = 朝 +x，90 = 朝 +y）')
    add.add_argument('--rad', action='store_true', help='--yaw 按弧度解释')
    add.add_argument('--actions', default=','.join(DEFAULT_ACTIONS),
                     help='到点动作，逗号分隔，默认 deliver,photo')
    add.add_argument('--force', action='store_true', help='同名时覆盖')

    setp = sub.add_parser('set', parents=[common], help='修改地点')
    setp.add_argument('name', help='地点名或编号')
    setp.add_argument('--x', type=float, default=None)
    setp.add_argument('--y', type=float, default=None)
    setp.add_argument('--yaw', type=float, default=None,
                      help='朝向，默认单位度')
    setp.add_argument('--rad', action='store_true', help='--yaw 按弧度解释')
    setp.add_argument('--actions', default=None, help='到点动作，逗号分隔')
    setp.add_argument('--rename', default=None, help='改名')

    dele = sub.add_parser('del', parents=[common],
                          help='删除地点（名字或编号）')
    dele.add_argument('name', help='地点名或编号（从 1 开始）')
    dele.add_argument('-y', '--yes', action='store_true', help='不再确认')

    home = sub.add_parser('home', parents=[common], help='设置出发点')
    home.add_argument('--x', type=float, default=None)
    home.add_argument('--y', type=float, default=None)
    home.add_argument('--yaw', type=float, default=None,
                      help='朝向，默认单位度')
    home.add_argument('--rad', action='store_true', help='--yaw 按弧度解释')

    clear = sub.add_parser('clear', parents=[common], help='清空全部地点')
    clear.add_argument('-y', '--yes', action='store_true', help='不再确认')

    sub.add_parser('pose', parents=[common], help='打印机器人当前位姿')
    here = sub.add_parser('here', parents=[common],
                          help='用机器人当前位姿建点')
    here.add_argument('name', help='地点名')
    here.add_argument('--actions', default=','.join(DEFAULT_ACTIONS))
    here.add_argument('--force', action='store_true', help='同名时覆盖')

    return parser


COMMANDS = {
    'list': cmd_list,
    'add': cmd_add,
    'set': cmd_set,
    'del': cmd_del,
    'home': cmd_home,
    'clear': cmd_clear,
    'pose': cmd_pose,
    'here': cmd_here,
}


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)

    path = os.path.expanduser(args.file) if getattr(args, 'file', None) \
        else default_places_file()
    data = load_places(path)

    handler = COMMANDS.get(getattr(args, 'action', None))
    if handler is None:
        print('地点表：%s' % path)
        interactive(data, path)
        return 0

    return handler(args, data, path)


if __name__ == '__main__':
    # 过滤掉 ROS 的 __name:=xxx 之类的重映射参数，argparse 不认
    raw = [item for item in sys.argv[1:] if ':=' not in item]
    try:
        sys.exit(main(raw))
    except KeyboardInterrupt:
        print('')
        sys.exit(130)

# spark_deliver —— 送物小车

选一个地点，机器人把东西送过去；到点语音提示、自动拍照留档，取走之后自己回出发点。
顺手把“拍到的照片”整理成一个能直接打开的网页索引。

```
选地点 → 导航过去 → 拍照留档 → 语音提示"请取走" → 按键确认 → 返回出发点
```

这个包**只调用**已有的导航栈和相机驱动，不修改 `final_project`、`spark_navigation`、
`spark_vision`、`spark_patrol` 等任何现有功能包。

---

## 1. 能干什么

| 功能 | 说明 |
|---|---|
| 送物 | 选目的地，导航过去并语音提示，等人取走，超时自动返回 |
| 拍照留档 | 每次到达自动拍一张，存盘并写入网页索引（送达存证） |
| 巡拍一圈 | `task:=photo_round`，按地点表走一圈，每个点拍一张，当拍照工具用 |
| 终端面板 | `cmd_panel.py`，不想敲 rostopic 时用编号选地点、确认、取消 |
| 网页索引 | `index.html`，缩略图 + 地点 + 时间，课设演示/报告里直接截图 |

## 2. 目录结构

```
spark_deliver/
├── package.xml / CMakeLists.txt
├── config/
│   ├── places.yaml      # 地点表（导航点，坐标自己标定）
│   ├── deliver.yaml     # 送物参数：超时、重试、等取件时长、播报开关
│   └── photo.yaml       # 拍照参数：图像话题、存盘目录、缩略图宽度
├── launch/
│   ├── deliver.launch       # 一键：驱动 + 地图 + 定位 + 导航 + 业务节点
│   ├── deliver_nodes.launch # 只起业务节点（导航栈已在跑时用）
│   └── photo_round.launch   # 巡拍模式
├── scripts/
│   ├── deliver_node.py   # 送物状态机（主流程）
│   ├── photo_node.py     # 抓拍存盘 + 更新索引
│   ├── make_report.py    # 生成 index.html
│   ├── place_manager.py  # 地点管理：手动输入 / 修改 / 删除导航点
│   ├── teach_waypoints.py# 示教授点：遥控到位按键记航点
│   ├── cmd_panel.py      # 终端操作面板
│   ├── tts_bridge.py     # /voice/tts → 本机 ekho 播报
│   ├── sim_nav_server.py # 无真机时模拟 move_base（自测用）
│   └── sim_camera.py     # 无真机时模拟相机（自测用）
├── rviz/deliver.rviz
└── doc/
    ├── 操作手册.md       # 现场怎么一步步跑
    └── 设计说明.md       # 设计思路，写报告可以直接用
```

## 3. 依赖

- ROS Noetic，已有导航栈：`map_server` / `amcl` / `move_base`
- 相机驱动（astra 或 d435）、雷达驱动
- Python：`rospy` `actionlib` `cv_bridge` `tf` `tf2_ros` `yaml` `opencv`
- 语音播报：本机 `ekho`（`which ekho` 能查到即可）

## 4. 编译

```bash
cd ~/spark_noetic
catkin_make --pkg spark_deliver
source devel/setup.bash
```

## 5. 三步跑起来

**第一步：标定地点**（两种方式，示教最省事）

*方式一 · 遥控示教（推荐）*：把车开到地方，按一下键就记下来，不用看坐标

```bash
# 终端 1：只起地图 + 定位 + 导航，不起送物节点
roslaunch spark_deliver deliver.launch start_deliver:=false

# 终端 2：进示教模式
rosrun spark_deliver teach_waypoints.py
```

| 键 | 作用 |
|---|---|
| `空格` | 记一个航点，自动命名 航点1、航点2…… |
| `n` | 记一个航点，自己取名 |
| `h` | 把当前位置设为出发点 |
| `u` | 撤销上一个航点 |
| `l` | 看已记录了哪些 |
| `q` | 退出 |

流程：进示教 → 按 `h` 记出发点 → 遥控走到第一个点 → 空格 → 走到第二个点 → 空格 → … → `q` 退出。
退出时会自动通知送物节点重载地点表，**不用重启**。

没有遥控器就用自带的键盘遥控，一个终端搞定：

```bash
rosrun spark_deliver teach_waypoints.py --teleop
# w 前进  s 后退  a 左转  d 右转（大写 2 倍速），松手 0.8 秒自动停，空格停车并记点
```

> 示教前先确认 AMCL 定位是对的（RViz 里机器人位置跟实际对得上）。如果起点不是地图原点，
> 先用 RViz 的 `2D Pose Estimate` 给一次初始位姿，再按 `h`。

*方式二 · 手动填数字*：不开 ROS 时用，或者事后微调

```bash
rosrun spark_deliver place_manager.py            # 交互菜单：列出 / 新增 / 修改 / 删除 / 清空
rosrun spark_deliver place_manager.py list       # 只看现有哪些点

rosrun spark_deliver place_manager.py add 前台 --x 1.20 --y 0.50 --yaw 90
rosrun spark_deliver place_manager.py set 前台 --x 1.35 --yaw 45     # 只改给出的字段
rosrun spark_deliver place_manager.py del 前台                       # 名字或编号都行
rosrun spark_deliver place_manager.py del 2

rosrun spark_deliver place_manager.py home --x 0 --y 0 --yaw 0       # 出发点
rosrun spark_deliver place_manager.py clear                          # 清空（出发点保留）
```

- `--yaw` 用**度**：`0` 朝 map 的 +x，`90` 朝 +y，`180` 朝 -x，`270` 朝 -y
- 每次写入前自动备份成 `places.yaml.bak`
- 同名再加会被拒绝，确实要覆盖加 `--force`
- 也支持读当前位姿：`pose` 只打印，`here 名字` 直接存（都要开着定位）

**第二步：启动**

```bash
roslaunch spark_deliver deliver.launch \
    camera_type:=d435 lidar_type:=ydlidar_g6
```

地点表默认就是 `config/places.yaml`，不用另外传路径。

> 相机型号和雷达型号按实际改；`astra/astrapro` 会自动把图像话题切成 `/camera/rgb/image_raw`。

**第三步：操作**

```bash
rosrun spark_deliver cmd_panel.py
```

面板里按编号选地点，`c` 确认取件，`x` 取消，`p` 巡拍，`h` 回出发点，`r` 重建索引。

也可以直接发话题：

```bash
rostopic pub -1 /spark_deliver/command std_msgs/String "data: '1'"       # 送 1 号地点
rostopic pub -1 /spark_deliver/command std_msgs/String "data: '前台'"     # 按名字
rostopic pub -1 /spark_deliver/command std_msgs/String "data: 'confirm'"  # 已取走
rostopic pub -1 /spark_deliver/command std_msgs/String "data: 'cancel'"   # 取消
```

照片和索引在：

```
~/.spark_deliver/photos/
├── 2026-11-02/143012_前台.jpg
├── thumbs/
├── meta.jsonl
└── index.html      ← 浏览器直接打开
```

## 6. 接口一览

| 类型 | 名称 | 说明 |
|---|---|---|
| 话题 | `/spark_deliver/command` | 指令：编号、地点名、list/confirm/cancel/photo/back |
| 话题 | `/spark_deliver/status` | 状态 JSON（state / text / place / places），latched |
| 话题 | `/spark_deliver/photo_event` | 到点拍照事件 JSON |
| 话题 | `/spark_deliver/photo_saved` | 拍照完成通知 JSON |
| 话题 | `/voice/tts` | 语音播报文本（沿用工作区既有约定） |
| 服务 | `/spark_deliver/cancel` | 取消当前任务 |
| 服务 | `/spark_deliver/confirm` | 确认取件，立即返航 |
| 服务 | `/spark_deliver/reload` | 重新读取地点表（示教退出时自动调） |
| 动作 | `move_base` | 导航客户端 |

常用参数（`config/deliver.yaml`、`config/photo.yaml`）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `~goal_timeout` | 120 | 单段导航超时（秒） |
| `~max_retries` | 1 | 导航失败重试次数 |
| `~wait_pickup_timeout` | 60 | 到点等取件秒数，0 = 一直等 |
| `~return_home` | true | 送完是否回出发点 |
| `~photo_on_arrive` | true | 到点是否拍照 |
| `~image_topic` | `/camera/color/image_raw` | 彩色图话题 |
| `~save_dir` | `~/.spark_deliver/photos` | 照片根目录 |

## 7. 没有真机时怎么自测

```bash
# 三个终端
roscore
rosrun spark_deliver sim_nav_server.py
rosrun spark_deliver sim_camera.py

# 第四个终端
rosrun spark_deliver deliver_node.py \
    _places_file:=$PWD/config/places.yaml \
    _tts_enable:=false _publish_initial_pose:=false
rosrun spark_deliver photo_node.py _image_topic:=/camera/color/image_raw
rosrun spark_deliver cmd_panel.py
```

模拟导航会假装走 2 秒然后报成功，模拟相机会发一张带时间戳的合成图，
用来验证流程、状态机和拍照存盘是否正常，不需要机器人。

## 8. 常见问题

**一直不动，状态停在 navigating**
看 `move_base` 有没有起来、地图和 AMCL 是否正常；`rostopic echo /move_base/status` 有没有 ACTIVE。
出发点和目标点之间如果被障碍堵死，也会一直规划失败。

**定位飘 / 一开始就跑偏**
`deliver.launch` 默认会把 `places.yaml` 里的出发点当初始位姿发给 AMCL。
如果机器人不是停在出发点上启动的，把 `publish_initial_pose:=false`，用 RViz 的
`2D Pose Estimate` 手动给一次。

**没声音**
`which ekho` 确认装了；`start_tts:=false` 时不会起播报节点。
也可以自己起 `spark_voice` 的 TTS，只要它订阅 `/voice/tts`。

**照片是黑的 / 没存下来**
`rostopic hz` 看一眼图像话题有没有数据；相机型号和 `image_topic` 对不上是最常见原因。
astra 是 `/camera/rgb/image_raw`，d435 是 `/camera/color/image_raw`。

**面板里没有地点可选**

`config/places.yaml` 默认是空的，就是要你自己标。先用 `place_manager.py` 加至少一个点：

```bash
rosrun spark_deliver place_manager.py add 前台 --x 1.2 --y 0.5 --yaw 90
```

**改完地点表没生效**

示教工具退出时会自动重载，不用管。手动改完 `places.yaml` 的话，让它重读一次：

```bash
rosservice call /spark_deliver/reload "{}"
```

不想调服务就重启节点（或整个 `roslaunch`）。正在送物时不能重载，先按 `x` 取消。

**地点名重复了**

`add` 遇到同名会拒绝，确实要覆盖加 `--force`；改名用 `set 旧名 --rename 新名`。
`place_manager.py list` 随时看当前有哪些点。

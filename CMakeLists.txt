cmake_minimum_required(VERSION 3.0.2)
project(spark_deliver)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  std_srvs
  geometry_msgs
  sensor_msgs
  nav_msgs
  actionlib
  move_base_msgs
  cv_bridge
  tf
  tf2_ros
)

catkin_package(
  CATKIN_DEPENDS
    rospy std_msgs std_srvs geometry_msgs sensor_msgs nav_msgs
    actionlib move_base_msgs cv_bridge tf tf2_ros
)

catkin_install_python(PROGRAMS
  scripts/deliver_node.py
  scripts/photo_node.py
  scripts/make_report.py
  scripts/place_manager.py
  scripts/teach_waypoints.py
  scripts/cmd_panel.py
  scripts/tts_bridge.py
  scripts/sim_nav_server.py
  scripts/sim_camera.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY config launch rviz doc
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

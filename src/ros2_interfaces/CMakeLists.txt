cmake_minimum_required(VERSION 3.8)
project(ros2_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# required ROS2 build tools
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)  # turns custom interfaces into things ROS2 can understand

# for the interface types
find_package(std_msgs REQUIRED)

# specify interface files to build
set(msg_files
  "msg/RobotState.msg"
)

set(srv_files
  "srv/ComputeIK.srv"
)

set(action_files
  "action/CommandGripper.action"
  "action/MoveStraight.action"
  "action/MoveToJoints.action"
  "action/MoveToPose.action"
  "action/ServoToTarget.action"
)

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  ${action_files}
  DEPENDENCIES std_msgs
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def _app_node(transport):
    """The phone transport selected by app:=<transport>; they are alternatives, so only one runs."""
    return Node(
        package='app_interface',
        executable=f'{transport}_interface',
        name=f'{transport}_interface_node',
        output='screen',
        condition=IfCondition(
            PythonExpression(["'", LaunchConfiguration('app'), "' == '", transport, "'"])),
    )


# robot.launch.py plus the Gemini agent
def generate_launch_description():
    robot_ip_arg = DeclareLaunchArgument('robot_ip', default_value='192.168.1.10')
    username_arg = DeclareLaunchArgument('username', default_value='admin')
    password_arg = DeclareLaunchArgument('password', default_value='admin')
    rviz_arg = DeclareLaunchArgument('rviz', default_value='false')
    app_arg = DeclareLaunchArgument(
        'app', default_value='none',
        description='phone transport to start: ble | bluetooth | network | none')

    robot_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([FindPackageShare('kinova_bringup'), 'launch', 'robot.launch.py'])
        ]),
        launch_arguments={
            'robot_ip': LaunchConfiguration('robot_ip'),
            'username': LaunchConfiguration('username'),
            'password': LaunchConfiguration('password'),
            'rviz': LaunchConfiguration('rviz'),
        }.items()
    )

    agent_node = Node(
        package='gemini_agent',
        executable='agent_node',
        name='gemini_agent',
        output='screen',
        # only forward an explicitly set config path; otherwise gemini_agent.config
        # finds the config.yaml at the root of this checkout
        additional_env=({'KINOVA_GEMINI_CONFIG': os.environ['KINOVA_GEMINI_CONFIG']}
                        if os.environ.get('KINOVA_GEMINI_CONFIG') else {}),
    )

    return LaunchDescription([
        robot_ip_arg,
        username_arg,
        password_arg,
        rviz_arg,
        app_arg,
        robot_launch,
        agent_node,
        _app_node('ble'),
        _app_node('bluetooth'),
        _app_node('network'),
        LogInfo(msg='gemini_agent starting - open web/index.html in a browser for the UI'),
    ])

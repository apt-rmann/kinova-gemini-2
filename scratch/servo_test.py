"""Bench test for the ServoToTarget primitive - no camera, no face.

Fixes a base-frame target relative to where the TCP is now, then streams the TCP -> target
vector on /servo_vector at the /robot_state rate (20 Hz) while the goal runs.

    python scratch/servo_test.py --dy 0.05                        # 5 cm to the left
    python scratch/servo_test.py --dx -0.05 --dz 0.03
    python scratch/servo_test.py --dy 0.05 --sine 0.03 --stop 0   # target bobs 3 cm in z; tracks until Ctrl-C
    python scratch/servo_test.py --dy 0.10 --quit-after 1.0       # stream dies mid-move: the arm must halt

Gains and limits are controller params read at startup (servo_kp, servo_kd, servo_max_speed,
servo_vector_timeout_s, servo_timeout_s), so tuning them needs a controller restart, not a rebuild:
    ros2 run kortex_controller controller --ros-args -p servo_kp:=1.5 -p servo_kd:=0.05
"""

import argparse
import asyncio
import math
import sys
import threading
import time

import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Empty

from gemini_agent.config import load_config
from gemini_agent.robot import Robot
from ros2_interfaces.msg import RobotState


class Streamer:
    """Publishes target - TCP on every RobotState while active."""

    def __init__(self, robot, target, sine, period):
        self.robot = robot
        self.target = target
        self.sine = sine
        self.period = period
        self.active = False
        self.t0 = 0.0
        self.last_print = -1.0

    def start(self):
        self.t0 = time.monotonic()
        self.active = True

    def on_state(self, msg):
        if not self.active:
            return
        t = time.monotonic() - self.t0
        target = self.target.copy()
        if self.sine:
            target[2] += self.sine * math.sin(2 * math.pi * t / self.period)
        vector = target - np.array([msg.x, msg.y, msg.z])
        self.robot.publish_servo_vector(vector)
        if t - self.last_print >= 0.5:
            print(f't={t:5.1f} s   to go {np.linalg.norm(vector) * 100:5.1f} cm')
            self.last_print = t


async def run(robot, streamer, args):
    streamer.start()
    if args.quit_after is not None:
        def quit_streaming():
            streamer.active = False
            print(f'--- stopped streaming after {args.quit_after} s ---')
        asyncio.get_running_loop().call_later(args.quit_after, quit_streaming)

    res = await robot.servo_to_target(args.stop)
    streamer.active = False
    print(f'{res.status}: {res.message}')
    print(f'robot: {robot.state()}   target was {np.round(streamer.target, 3).tolist()}')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--dx', type=float, default=0.0, help='target offset from the current TCP, base x, m')
    p.add_argument('--dy', type=float, default=0.0, help='target offset from the current TCP, base y, m')
    p.add_argument('--dz', type=float, default=0.0, help='target offset from the current TCP, base z, m')
    p.add_argument('--stop', type=float, default=0.005, help='stop_distance, m (0 = never succeed)')
    p.add_argument('--sine', type=float, default=0.0, help='z amplitude of a moving target, m')
    p.add_argument('--period', type=float, default=6.0, help='period of the moving target, s')
    p.add_argument('--quit-after', type=float, default=None, help='stop streaming after this many s')
    args = p.parse_args()

    cfg = load_config()

    rclpy.init()
    node = Node('servo_test')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    robot = None
    try:
        robot = Robot(node, cfg)
        missing = robot.wait_for_servers(5.0)
        if missing:
            print(f'action servers not available: {missing}', file=sys.stderr)
            return
        if not robot.wait_for_state(2.0):
            print('no RobotState received - is the controller running?', file=sys.stderr)
            return

        pose = robot.current_pose()
        target = np.array(pose[:3]) + np.array([args.dx, args.dy, args.dz])
        print(f'TCP {np.round(pose[:3], 3).tolist()} -> target {np.round(target, 3).tolist()}')

        streamer = Streamer(robot, target, args.sine, args.period)
        node.create_subscription(RobotState, cfg['ros']['state_topic'], streamer.on_state, 10)
        asyncio.run(run(robot, streamer, args))
    except KeyboardInterrupt:
        print('\ninterrupted - stopping arm')
        if robot is not None:
            robot._stop_pub.publish(Empty())
            time.sleep(0.2)
    finally:
        executor.shutdown()
        spin_thread.join(timeout=2.0)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

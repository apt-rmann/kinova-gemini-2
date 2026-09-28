"""Async interface to the kortex_controller action servers."""

import asyncio
import time
from dataclasses import dataclass

from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient
from std_msgs.msg import Empty

from ros2_interfaces.action import CommandGripper, MoveStraight, MoveToJoints, MoveToPose
from ros2_interfaces.msg import RobotState

_STATUS = {
    GoalStatus.STATUS_SUCCEEDED: 'succeeded',
    GoalStatus.STATUS_ABORTED: 'aborted',
    GoalStatus.STATUS_CANCELED: 'canceled',
}


@dataclass
class ActionResult:
    ok: bool
    status: str      # succeeded | aborted | canceled | unavailable | rejected
    message: str = ''


class Robot:
    """Owns the four action clients and the latest RobotState."""

    def __init__(self, node, cfg):
        self.node = node
        self.cfg = cfg
        self.presets = cfg['presets']

        ros = cfg['ros']
        self._clients = {
            'command_gripper': ActionClient(node, CommandGripper, 'command_gripper'),
            'move_straight': ActionClient(node, MoveStraight, 'move_straight'),
            'move_to_joints': ActionClient(node, MoveToJoints, 'move_to_joints'),
            'move_to_pose': ActionClient(node, MoveToPose, 'move_to_pose'),
        }

        self._state = None
        node.create_subscription(RobotState, ros['state_topic'], self._on_state, 10)
        self._stop_pub = node.create_publisher(Empty, ros['stop_topic'], 10)

        # ClientGoalHandle is not hashable, so key active goals by their uuid
        self._active_goals = {}

        # the model only sees orientation when the raw 6-DoF pose tool is enabled
        self._include_theta = 'move_to_pose' in (cfg.get('tools') or [])

    def _on_state(self, msg):
        self._state = msg

    def wait_for_servers(self, timeout_sec: float = 5.0) -> list:
        """Wait once, at startup. Returns the names of any servers that did not come up."""
        missing = []
        for name, client in self._clients.items():
            if not client.wait_for_server(timeout_sec=timeout_sec):
                missing.append(name)
        return missing

    def wait_for_state(self, timeout_sec: float = 2.0) -> bool:
        """Wait for the first RobotState so the first tool call is not a cold read."""
        deadline = time.monotonic() + timeout_sec
        while self._state is None and time.monotonic() < deadline:
            time.sleep(0.02)
        return self._state is not None

    # --- primitives ---

    async def command_gripper(self, position: float) -> ActionResult:
        """0 = fully open, 1 = fully closed."""
        goal = CommandGripper.Goal()
        goal.position = float(position)
        return await self._send_goal('command_gripper', goal)

    async def move_straight(self, distance: float) -> ActionResult:
        """Straight-line move along the tool's own +Z."""
        goal = MoveStraight.Goal()
        goal.distance = float(distance)
        return await self._send_goal('move_straight', goal)

    async def move_to_joints(self, angles) -> ActionResult:
        goal = MoveToJoints.Goal()
        goal.joint_angles = [float(a) for a in angles]
        return await self._send_goal('move_to_joints', goal)

    async def move_to_pose(self, x, y, z, theta_x, theta_y, theta_z) -> ActionResult:
        """Meters and Kortex extrinsic XYZ Euler degrees."""
        goal = MoveToPose.Goal()
        goal.x = float(x)
        goal.y = float(y)
        goal.z = float(z)
        goal.theta_x = float(theta_x)
        goal.theta_y = float(theta_y)
        goal.theta_z = float(theta_z)
        return await self._send_goal('move_to_pose', goal)

    async def move_to_preset(self, name: str) -> ActionResult:
        angles = self.presets.get(name)
        if angles is None:
            known = ', '.join(self.presets)
            return ActionResult(False, 'rejected', f'unknown preset {name!r}; known presets: {known}')
        return await self.move_to_joints(angles)

    # --- helpers ---

    def state(self) -> dict:
        """What the model sees after every action."""
        if self._state is None:
            return {'error': 'no robot state yet'}
        s = self._state
        out = {
            'ee_xyz': [round(float(s.x), 3), round(float(s.y), 3), round(float(s.z), 3)],
            'gripper': round(float(s.gripper_position), 2),
        }
        if self._include_theta:
            out['ee_theta_deg'] = [int(round(float(s.theta_x))),
                                   int(round(float(s.theta_y))),
                                   int(round(float(s.theta_z)))]
        return out

    def current_pose(self):
        """Raw Kortex pose (x, y, z, theta_x, theta_y, theta_z), or None if no state yet."""
        if self._state is None:
            return None
        s = self._state
        return (float(s.x), float(s.y), float(s.z),
                float(s.theta_x), float(s.theta_y), float(s.theta_z))

    async def stop(self):
        """Halt the arm in the C++ node, then cancel whatever goals we have open."""
        self._stop_pub.publish(Empty())
        for goal_handle in list(self._active_goals.values()):
            try:
                await self._await_rclpy_future(goal_handle.cancel_goal_async())
            except Exception as e:  # a goal that already finished is not an error
                self.node.get_logger().warn(f'error canceling goal: {e}')
        self._active_goals.clear()

    def interrupted_result(self, reason: str) -> dict:
        return {'ok': False, 'status': 'interrupted', 'reason': reason, 'robot': self.state()}

    # --- internals ---

    async def _await_rclpy_future(self, rclpy_future):
        """Convert an rclpy.task.Future to an asyncio.Future without blocking the event loop."""
        loop = asyncio.get_running_loop()
        asyncio_future = loop.create_future()

        def callback(future):
            def set_res():
                if not asyncio_future.done():
                    asyncio_future.set_result(future.result())
            loop.call_soon_threadsafe(set_res)

        rclpy_future.add_done_callback(callback)
        return await asyncio_future

    async def _send_goal(self, name: str, goal) -> ActionResult:
        client = self._clients[name]
        if not client.server_is_ready():
            return ActionResult(False, 'unavailable', f'action server {name} is not available')

        goal_handle = await self._await_rclpy_future(client.send_goal_async(goal))
        if not goal_handle.accepted:
            return ActionResult(False, 'rejected', 'goal rejected')

        key = bytes(goal_handle.goal_id.uuid)
        self._active_goals[key] = goal_handle
        try:
            res = await self._await_rclpy_future(goal_handle.get_result_async())
            return self._to_action_result(res)
        except asyncio.CancelledError:
            # stop the arm before the tool unwinds
            await asyncio.shield(self._await_rclpy_future(goal_handle.cancel_goal_async()))
            raise
        finally:
            self._active_goals.pop(key, None)

    @staticmethod
    def _to_action_result(res) -> ActionResult:
        status = _STATUS.get(res.status, 'aborted')
        success = bool(getattr(res.result, 'success', False))
        message = str(getattr(res.result, 'message', ''))
        return ActionResult(success and status == 'succeeded', status, message)

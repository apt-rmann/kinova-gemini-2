"""Motion tools. Each one runs a single robot primitive and reports the resulting state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import tool

if TYPE_CHECKING:
    from . import ToolContext


def _result(res, robot, **extra) -> dict:
    out = {'ok': res.ok, 'status': res.status, 'robot': robot.state()}
    if res.message:
        out['message'] = res.message
    out.update(extra)
    return out


def _error(robot, message: str) -> dict:
    return {'ok': False, 'status': 'error', 'message': message, 'robot': robot.state()}


@tool(
    name='move_to',
    description=('Move the end effector to base-frame position (x, y, z) in meters, '
                 'keeping the current gripper orientation.'),
    parameters={
        'type': 'object',
        'properties': {
            'x': {'type': 'number', 'description': 'base-frame x in meters'},
            'y': {'type': 'number', 'description': 'base-frame y in meters'},
            'z': {'type': 'number', 'description': 'base-frame z in meters'},
        },
        'required': ['x', 'y', 'z'],
    },
)
async def move_to(ctx: 'ToolContext', x: float, y: float, z: float) -> dict:
    pose = ctx.robot.current_pose()
    if pose is None:
        return _error(ctx.robot, 'no robot state yet')
    res = await ctx.robot.move_to_pose(x, y, z, pose[3], pose[4], pose[5])
    return _result(res, ctx.robot)


@tool(
    name='move_relative',
    description=('Translate the end effector by (dx, dy, dz) meters in the base frame, keeping '
                 'orientation. Use for small corrections (e.g. dy=0.02 moves 2 cm toward +y).'),
    parameters={
        'type': 'object',
        'properties': {
            'dx': {'type': 'number', 'description': 'base-frame x offset in meters'},
            'dy': {'type': 'number', 'description': 'base-frame y offset in meters'},
            'dz': {'type': 'number', 'description': 'base-frame z offset in meters'},
        },
        'required': [],
    },
)
async def move_relative(ctx: 'ToolContext', dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> dict:
    pose = ctx.robot.current_pose()
    if pose is None:
        return _error(ctx.robot, 'no robot state yet')
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return _error(ctx.robot, 'at least one of dx, dy, dz must be nonzero')
    res = await ctx.robot.move_to_pose(pose[0] + dx, pose[1] + dy, pose[2] + dz,
                                       pose[3], pose[4], pose[5])
    return _result(res, ctx.robot)


@tool(
    name='move_straight',
    description=('Move straight along the direction the gripper is pointing by distance meters '
                 '(negative = backwards). Use for approaching and backing away from objects.'),
    parameters={
        'type': 'object',
        'properties': {
            'distance': {'type': 'number', 'description': 'meters along the gripper axis'},
        },
        'required': ['distance'],
    },
)
async def move_straight(ctx: 'ToolContext', distance: float) -> dict:
    res = await ctx.robot.move_straight(distance)
    return _result(res, ctx.robot)


@tool(
    name='set_gripper',
    description='Set the gripper: 0.0 = fully open, 1.0 = fully closed.',
    parameters={
        'type': 'object',
        'properties': {
            'position': {'type': 'number', 'description': '0.0 open to 1.0 closed'},
        },
        'required': ['position'],
    },
)
async def set_gripper(ctx: 'ToolContext', position: float) -> dict:
    if not 0.0 <= float(position) <= 1.0:
        return _error(ctx.robot, 'position must be between 0.0 and 1.0')
    res = await ctx.robot.command_gripper(position)
    return _result(res, ctx.robot)


@tool(
    name='go_to_preset',
    description=("Move to a named joint configuration. 'home' is the default position; "
                 "'user' faces the user."),
    parameters={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'preset name', 'enum': []},
        },
        'required': ['name'],
    },
)
async def go_to_preset(ctx: 'ToolContext', name: str) -> dict:
    res = await ctx.robot.move_to_preset(name)
    return _result(res, ctx.robot)


@tool(
    name='stop',
    description='Immediately stop all robot motion and hold position.',
    parameters={'type': 'object', 'properties': {}, 'required': []},
)
async def stop(ctx: 'ToolContext') -> dict:
    await ctx.robot.stop()
    return {'ok': True, 'status': 'stopped', 'robot': ctx.robot.state()}


@tool(
    name='move_to_pose',
    description=('Move to a full 6-DoF pose: position (m) and Kortex extrinsic XYZ Euler angles '
                 'theta_x, theta_y, theta_z (deg).'),
    parameters={
        'type': 'object',
        'properties': {
            'x': {'type': 'number', 'description': 'base-frame x in meters'},
            'y': {'type': 'number', 'description': 'base-frame y in meters'},
            'z': {'type': 'number', 'description': 'base-frame z in meters'},
            'theta_x': {'type': 'number', 'description': 'degrees'},
            'theta_y': {'type': 'number', 'description': 'degrees'},
            'theta_z': {'type': 'number', 'description': 'degrees'},
        },
        'required': ['x', 'y', 'z', 'theta_x', 'theta_y', 'theta_z'],
    },
)
async def move_to_pose(ctx: 'ToolContext', x: float, y: float, z: float,
                       theta_x: float, theta_y: float, theta_z: float) -> dict:
    res = await ctx.robot.move_to_pose(x, y, z, theta_x, theta_y, theta_z)
    return _result(res, ctx.robot)

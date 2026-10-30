"""The pick skill: preshape, approach, close.

Closing a fully-open gripper onto an object drags or tips it before the pads meet. This
skill instead opens the fingers to just wider than the object, drives them around it, and
then closes to just narrower than it, so the object is captured rather than swept.

The sequence, from the width and depth that `look` reports:

    1  preshape the gripper to (width + CLEARANCE_MM)  -- fingers clear the object
    2  move to the object's centroid, keeping the current orientation
    3  move straight by (depth / 2) so the pads straddle the object's middle
    4  close the gripper to (width - SQUEEZE_MM)       -- fingers load against it

Orientation is never changed: point the gripper at the object first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from . import skill
from .motion import _error, _result

if TYPE_CHECKING:
    from . import SkillContext

# ---------------------------------------------------------------- TUNING ----
CLEARANCE_MM = 25.0   # preshape opening = object width + this
SQUEEZE_MM = 8.0      # final opening = object width - this (the fingers' bite)

MAX_DEPTH_MM = 120.0  # cap on the model's depth guess, so a bad one cannot drive the
                      # arm through the table
MIN_DEPTH_MM = 0.0

# Gripper command (0 open .. 1 closed) against the measured opening between the pads,
# in millimeters. Measured on hardware; interpolated linearly in between.
GRIPPER_TABLE_MM = [143.0, 131.0, 101.0, 70.0, 38.0, 21.5, 5.5, 0.0]
GRIPPER_TABLE_CMD = [0.0, 0.1, 0.3, 0.5, 0.7, 0.8, 0.9, 0.98]
# ----------------------------------------------------------------------------


def gripper_for_width(width_mm: float) -> float:
    """Gripper command that leaves `width_mm` between the pads.

    Outside the table the command is clamped: wider than 143 mm opens fully, narrower
    than 5.5 mm closes to 0.98, which is also what any object too thin for the table to
    resolve ends up doing.
    """
    # np.interp needs an ascending x, and the table runs from open to closed
    return float(np.interp(width_mm, GRIPPER_TABLE_MM[::-1], GRIPPER_TABLE_CMD[::-1]))


@skill(
    name='pick',
    description=('Grasp an object with the gripper, given the centroid, width and depth that '
                 '`look` reported for it. Opens the fingers wider than the object, moves to it, '
                 'advances to its middle, and closes onto it. Does not change the gripper '
                 'orientation.'),
    parameters={
        'type': 'object',
        'properties': {
            'x': {'type': 'number', 'description': "base-frame x of the object's centroid, meters"},
            'y': {'type': 'number', 'description': "base-frame y of the object's centroid, meters"},
            'z': {'type': 'number', 'description': "base-frame z of the object's centroid, meters"},
            'width_mm': {'type': 'number',
                         'description': 'width_mm from look: how wide the object is across the '
                                        'gripper fingers'},
            'depth_mm': {'type': 'number',
                         'description': 'depth_mm from look: how far the object extends away '
                                        'from the camera'},
        },
        'required': ['x', 'y', 'z', 'width_mm', 'depth_mm'],
    },
)
async def pick(ctx: 'SkillContext', x: float, y: float, z: float,
               width_mm: float, depth_mm: float) -> dict:
    width_mm = float(width_mm)
    if width_mm <= 0.0:
        return _error(ctx.robot, f'width_mm must be positive, got {width_mm}')
    if width_mm > GRIPPER_TABLE_MM[0]:
        return _error(ctx.robot,
                      f'the object is {width_mm:.0f} mm wide; the gripper only opens to '
                      f'{GRIPPER_TABLE_MM[0]:.0f} mm')

    depth_mm = min(max(float(depth_mm), MIN_DEPTH_MM), MAX_DEPTH_MM)

    pose = ctx.robot.current_pose()
    if pose is None:
        return _error(ctx.robot, 'no robot state yet')

    preshape = gripper_for_width(width_mm + CLEARANCE_MM)
    final = gripper_for_width(max(width_mm - SQUEEZE_MM, 0.0))
    advance = depth_mm / 2.0 / 1000.0   # mm -> m

    ctx.emit('pick', {'target': [x, y, z], 'width_mm': width_mm, 'depth_mm': depth_mm,
                      'preshape': round(preshape, 2), 'final': round(final, 2)})

    def failed(step: str, res):
        return _result(res, ctx.robot, step=step)

    # 1. open wider than the object, before anything is near it
    res = await ctx.robot.command_gripper(preshape)
    if not res.ok:
        return failed('preshape the gripper', res)

    # 2. to the centroid, keeping the current orientation
    res = await ctx.robot.move_to_pose(x, y, z, pose[3], pose[4], pose[5])
    if not res.ok:
        return failed('move to the object', res)

    # 3. in to the object's middle
    if advance > 0.0:
        res = await ctx.robot.move_straight(advance)
        if not res.ok:
            return failed('advance onto the object', res)

    # 4. close onto it
    res = await ctx.robot.command_gripper(final)
    if not res.ok:
        return failed('close the gripper', res)

    return _result(res, ctx.robot, width_mm=width_mm, depth_mm=depth_mm,
                   preshape=round(preshape, 2), closed_to=round(final, 2))

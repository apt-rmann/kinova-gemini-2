"""Attach and detach physical tools from the holster.

The holster sits in front of the arm at the `holster` joint preset. Each slot carries an
ArUco marker (DICT_4X4_50); `config.yaml`'s `tools:` block, which the operator maintains
by hand, gives each tool's marker id and length.

Both skills run the same fixed sequence, with different tuning:

    1  move to the `holster` preset (attach also closes the gripper to a known opening)
    2  detect the slot's marker, take `marker center + OFFSET_CAM` as the target point
    3  move above the target point, keeping the current orientation
    4  move straight along the tool axis to engage (attach) or seat (detach)
    5  open the gripper (attach) or close it back (detach)
    6  move straight back out

The numbers below were tuned on hardware with `scratch/holster_test.py`; its steps 1-6
are `attach` and its steps 8-13 are `detach`.

The sequence is tuned against the bare-gripper TCP. After an attach the TCP moves to the
tool tip (its `length` in `tools:`), so every later move refers to
the tip; detach puts the TCP back on the bare gripper before it starts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from gemini_agent.perception import aruco

from . import skill
from .motion import _error, _result

if TYPE_CHECKING:
    from . import SkillContext

# ---------------------------------------------------------------- TUNING ----
HOLSTER_PRESET = 'holster'     # preset the holster is visible from, in config.yaml

# target point = marker center + this offset, in meters, in the camera
# (physical_realsense_link, optical convention) frame:
#   +x = image right, +y = image down, +z = away from the camera (depth)
ATTACH_OFFSET_CAM = (0.00, 0.065, 0.0)
ATTACH_HOVER_Z = 0.03          # m added to the target point's base_link z
ATTACH_APPROACH = 0.08         # m along the tool axis (+ = forward)
ATTACH_RETREAT = -0.15         # m along the tool axis (- = backward)

DETACH_OFFSET_CAM = (0.00, 0.05, 0.0)
DETACH_HOVER_Z = 0.1
DETACH_APPROACH = 0.12
DETACH_RETREAT = -0.10

DETECT_TIMEOUT = 4.0           # s to wait for a camera frame taken since the arm parked

ATTACH_GRIPPER_START = 0.97    # the gripper is closed to here before an attach, so the
                               # release in step 5 starts from a known opening
GRIPPER_OPEN_BY = 0.10         # subtracted from the gripper position to attach,
                               # added back to detach (0 open .. 1 closed)
# ----------------------------------------------------------------------------

# what the gripper is currently holding, by holster name; None = nothing (or unknown,
# e.g. right after a restart)
_attached: str | None = None


def holster_names(cfg: dict) -> list:
    """Tool names from config.yaml's `tools:` block, in config order."""
    return [str(name) for name in (cfg.get('tools') or {})]


def _tool(cfg: dict, name: str):
    """A tool's `tools:` entry ({'marker': id, 'length': m}) by case-insensitive name, or None."""
    wanted = name.strip().lower()
    for tool_name, entry in (cfg.get('tools') or {}).items():
        if str(tool_name).strip().lower() == wanted:
            return entry
    return None


def tool_length(cfg: dict, name: str) -> float:
    """How far a holster tool extends the TCP along tool +Z, in meters. Defaults to 0,
    i.e. the TCP stays on the bare gripper."""
    entry = _tool(cfg, name)
    return float(entry.get('length', 0.0)) if entry else 0.0


def _marker_id(cfg: dict, name: str):
    """Marker id of a holster tool's slot, or None."""
    entry = _tool(cfg, name)
    return int(entry['marker']) if entry else None


async def _sequence(ctx: 'SkillContext', action: str, name: str, marker_id: int,
                    offset_cam, hover_z: float, approach: float,
                    gripper_delta: float, retreat: float,
                    gripper_start: float = None) -> dict:
    """The shared six-step holster sequence. -> a skill result dict."""

    def failed(step: str, res):
        return _result(res, ctx.robot, step=step)

    # 1. look at the holster, from a known gripper opening
    res = await ctx.robot.move_to_preset(HOLSTER_PRESET)
    if not res.ok:
        return failed('move to holster preset', res)
    parked = ctx.camera.now()   # the camera does not move again until step 3

    if gripper_start is not None:
        res = await ctx.robot.command_gripper(gripper_start)
        if not res.ok:
            return failed('close the gripper before approaching', res)

    # 2. find the slot. Any frame taken since the arm parked will do, however old it is by
    # the time we ask: TF is resolved at the frame's own stamp, so the viewpoint is right.
    # Demanding a frame newer than *now* just fails on a stalled stream.
    try:
        snapshot = await ctx.camera.fresh_snapshot(after=parked, timeout=DETECT_TIMEOUT)
    except TimeoutError as e:
        return _error(ctx.robot, f'no camera frame at the holster: {e}')
    found = aruco.locate(snapshot, marker_id, offset_cam)
    ctx.emit('holster', {'action': action, 'tool': name, 'marker_id': marker_id,
                         'found': found['found'], 'image_id': snapshot.image_id})
    if not found['found']:
        return _error(ctx.robot,
                      f'{found["reason"]} at the holster (markers seen: {found["seen"]}); '
                      f'{name!r} is marker {marker_id}')
    point = found['point_base']

    # 3. above the target point, keeping the current orientation
    pose = ctx.robot.current_pose()
    if pose is None:
        return _error(ctx.robot, 'no robot state yet')
    target = point + np.array([0.0, 0.0, hover_z])
    res = await ctx.robot.move_to_pose(*target, *pose[3:])
    if not res.ok:
        return failed('move above the holster slot', res)

    # 4. in along the tool axis
    res = await ctx.robot.move_straight(approach)
    if not res.ok:
        return failed('approach the slot', res)

    # 5. release or re-grip
    current = ctx.robot.gripper_position()
    if current is None:
        return _error(ctx.robot, 'no robot state yet')
    res = await ctx.robot.command_gripper(min(1.0, max(0.0, current + gripper_delta)))
    if not res.ok:
        return failed('move the gripper', res)

    # 6. back out
    res = await ctx.robot.move_straight(retreat)
    if not res.ok:
        return failed('back out of the slot', res)

    return _result(res, ctx.robot, tool=name, marker_id=marker_id)


@skill(
    name='attach',
    description=('Pick up a physical tool from the holster and attach it to the gripper. Give the '
                 'tool name as it appears on the holster. The arm moves to the holster, finds the '
                 "tool's slot, grips it, and backs out; nothing else needs to be done first. "
                 'Detach the current tool before attaching another one.'),
    parameters={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'holster tool name', 'enum': []},
        },
        'required': ['name'],
    },
)
async def attach(ctx: 'SkillContext', name: str) -> dict:
    global _attached

    marker_id = _marker_id(ctx.cfg, name)
    if marker_id is None:
        known = ', '.join(holster_names(ctx.cfg)) or '(the holster is empty)'
        return _error(ctx.robot, f'{name!r} is not on the holster; available: {known}')
    if _attached is not None:
        return _error(ctx.robot, f'{_attached!r} is already attached; detach it first')

    result = await _sequence(ctx, 'attach', name, marker_id, ATTACH_OFFSET_CAM,
                             ATTACH_HOVER_Z, ATTACH_APPROACH, -GRIPPER_OPEN_BY, ATTACH_RETREAT,
                             gripper_start=ATTACH_GRIPPER_START)
    if result['ok']:
        _attached = name
        # the tool is in hand either way, so _attached stands even if this fails
        res = await ctx.robot.set_tool_length(tool_length(ctx.cfg, name))
        if not res.ok:
            result = _result(res, ctx.robot, step='set the TCP to the tool tip')
    result['attached'] = _attached
    return result


@skill(
    name='detach',
    description=('Return the physical tool currently held by the gripper to its holster slot. '
                 'Give the same tool name that was attached. The arm moves to the holster, finds '
                 'the slot, seats the tool, releases it, and backs out.'),
    parameters={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'holster tool name', 'enum': []},
        },
        'required': ['name'],
    },
)
async def detach(ctx: 'SkillContext', name: str) -> dict:
    global _attached

    marker_id = _marker_id(ctx.cfg, name)
    if marker_id is None:
        known = ', '.join(holster_names(ctx.cfg)) or '(the holster is empty)'
        return _error(ctx.robot, f'{name!r} is not on the holster; available: {known}')
    # an unknown state (None) is not an error: the tool may have been attached before this
    # process started, e.g. by hand or by a separate skill_cli call
    if _attached is not None and _attached != name:
        return _error(ctx.robot, f'{_attached!r} is attached, not {name!r}')

    # the holster offsets were tuned against the bare-gripper TCP
    res = await ctx.robot.set_tool_length(0.0)
    if not res.ok:
        return _result(res, ctx.robot, step='reset the TCP to the bare gripper')

    result = await _sequence(ctx, 'detach', name, marker_id, DETACH_OFFSET_CAM,
                             DETACH_HOVER_Z, DETACH_APPROACH, GRIPPER_OPEN_BY, DETACH_RETREAT)
    if result['ok']:
        _attached = None
    result['attached'] = _attached
    return result

"""Bring the itch tool to a spot on the user's face, so they can scratch by moving against it.

    1  attach the itch tool from the holster, unless it is already held; attach moves the
       TCP to the tool's tip
    2  move to the `user` preset, where the camera sees the face
    3  find the face region on a camera frame -> a base-frame target point
    4  servo the tool tip to the target. A tracking task re-measures the target on every new
       camera frame, and a streaming task publishes target - TCP on /servo_vector. When the
       face drops out (too close for the camera), it keeps steering to the last good target.
    5  hold still for HOLD_S while the user scratches, then back off along the tool axis

The tool stays attached afterwards, so a follow-up itch skips step 1.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import numpy as np

from gemini_agent.perception import face

from . import holster, skill
from .motion import _error, _result

if TYPE_CHECKING:
    from . import SkillContext

# ---------------------------------------------------------------- TUNING ----
TOOL = 'itcher'                # holster tool name in config.yaml's `tools:`
USER_PRESET = 'user'           # preset the face is visible from
STOP_DISTANCE = 0.03          # m short of the face point where the servo stops
HOLD_S = 5.0                   # s to hold still at the face
RETREAT = -0.10                # m along the tool axis after the hold (- = backward)

FIRST_FIX_TIMEOUT = 3.0        # s to find the face region before giving up
STREAM_HZ = 20.0               # /servo_vector rate (matches /robot_state)
TARGET_SMOOTHING = 0.5         # weight of each new measurement in the target (1 = no smoothing)
MAX_JUMP = 0.03                # m; a measurement this far from the target is a bad reading, skipped
# ----------------------------------------------------------------------------


async def _first_fix(ctx: 'SkillContext', location: str):
    """-> (located dict, snapshot); keeps trying new frames until FIRST_FIX_TIMEOUT."""
    deadline = ctx.camera.now() + FIRST_FIX_TIMEOUT
    after = ctx.camera.now()
    found = {'found': False, 'reason': 'no camera frame', 'landmarks': None}
    snapshot = None
    while ctx.camera.now() < deadline:
        try:
            snapshot = await ctx.camera.fresh_snapshot(after=after, timeout=1.0)
        except TimeoutError:
            continue
        after = snapshot.stamp
        found = await asyncio.to_thread(face.locate, snapshot, location)
        if found['found']:
            break
    return found, snapshot


async def _track(ctx: 'SkillContext', location: str, target: dict, after: float):
    """Update target['point'] from every new camera frame where the region is found."""
    while True:
        try:
            snapshot = await ctx.camera.fresh_snapshot(after=after, timeout=1.0)
        except TimeoutError:
            continue   # the streamer keeps steering to the last target meanwhile
        after = snapshot.stamp
        found = await asyncio.to_thread(face.locate, snapshot, location)
        if not found['found']:
            target['lost'] += 1
            continue
        point = found['point_base']
        if np.linalg.norm(point - target['point']) > MAX_JUMP:
            target['rejected'] += 1
            continue
        target['point'] = (1.0 - TARGET_SMOOTHING) * target['point'] + TARGET_SMOOTHING * point
        target['updates'] += 1


async def _stream(ctx: 'SkillContext', target: dict):
    """Publish target - TCP at STREAM_HZ for as long as the servo runs."""
    while True:
        pose = ctx.robot.current_pose()
        if pose is not None:
            ctx.robot.publish_servo_vector(target['point'] - np.array(pose[:3]))
        await asyncio.sleep(1.0 / STREAM_HZ)


@skill(
    name='itch',
    description=('Bring the itch-scratching tool to a spot on the user\'s face, so they can scratch an '
                 'itch by moving their face against it. Handles everything: gets the tool from the '
                 'holster if it is not already held, faces the user, finds the spot, moves there, '
                 'holds still for a few seconds, then backs away. Left and right are the USER\'s own '
                 'left and right. Pick the closest location to what the user describes.'),
    parameters={
        'type': 'object',
        'properties': {
            'location': {'type': 'string', 'description': 'spot on the user\'s face',
                         'enum': list(face.FACE_REGIONS)},
        },
        'required': ['location'],
    },
)
async def itch(ctx: 'SkillContext', location: str) -> dict:
    if location not in face.FACE_REGIONS:
        return _error(ctx.robot, f'unknown location {location!r}; known: {", ".join(face.FACE_REGIONS)}')

    # 1. the tool; attach also puts the TCP at its tip
    if holster._attached != TOOL:
        result = await holster.attach(ctx, TOOL)
        if not result['ok']:
            result['step'] = f'attach the {TOOL}'
            return result

    # 2. face the user
    res = await ctx.robot.move_to_preset(USER_PRESET)
    if not res.ok:
        return _result(res, ctx.robot, step='move to the user preset')

    # 3. first fix on the face region
    found, snapshot = await _first_fix(ctx, location)
    if snapshot is not None:
        annotated = face.annotate(snapshot.rgb, found['landmarks'], location)
        ctx.emit('itch', {'location': location, 'found': found['found'], 'image_id': snapshot.image_id})
    if not found['found']:
        out = _error(ctx.robot, f'{found["reason"]} from the user preset')
        if snapshot is not None:
            out['_image'] = annotated
        return out

    # 4. servo to it, re-measuring as we go
    target = {'point': found['point_base'], 'updates': 0, 'lost': 0, 'rejected': 0}
    tasks = [asyncio.create_task(_track(ctx, location, target, snapshot.stamp)),
             asyncio.create_task(_stream(ctx, target))]
    try:
        res = await ctx.robot.servo_to_target(STOP_DISTANCE)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    tracking = {k: target[k] for k in ('updates', 'lost', 'rejected')}
    if not res.ok:
        return _result(res, ctx.robot, step=f'servo to the {location}', tracking=tracking, _image=annotated)

    # 5. let the user scratch, then back off
    await asyncio.sleep(HOLD_S)
    res = await ctx.robot.move_straight(RETREAT)
    if not res.ok:
        return _result(res, ctx.robot, step='back away from the face')

    return _result(res, ctx.robot, location=location, tracking=tracking, _image=annotated)

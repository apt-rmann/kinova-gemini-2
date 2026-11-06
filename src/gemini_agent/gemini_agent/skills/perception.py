"""The look skill: names in, base-frame centroids plus an annotated image out."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gemini_agent.perception import locate_objects

from . import skill

if TYPE_CHECKING:
    from . import SkillContext


@skill(
    name='look',
    description=("Locate objects or locations visible in the current camera image. Give one short name per "
                 "object type (e.g. 'red cup', 'wooden block','place to put cup'). Returns each object's centroid in "
                 "robot base-frame meters, its width_mm across the gripper fingers (measured), and "
                 'its depth_mm away from the camera (estimated). Pass all three to `pick` to grasp '
                 'it. You also receive an annotated image; check that each '
                 'labeled outline is on the right object. IDs are valid only for this call.'
                 'ONLY call look to return exact coordinates of items you can already see.'
                 'If the requested objects arent visible in the camera frame, move to the robot until you see them,'
                 'then call look to return their exact coordinates.'),
    parameters={
        'type': 'object',
        'properties': {
            'objects': {
                'type': 'array',
                'description': 'short names of the object types to locate',
                'items': {'type': 'string'},
                'minItems': 1,
                'maxItems': 8,
            },
        },
        'required': ['objects'],
    },
)
async def look(ctx: 'SkillContext', objects: list) -> dict:
    snapshot = await ctx.camera.fresh_snapshot(after=ctx.camera.now())

    found, annotated = await locate_objects(snapshot, list(objects), ctx.cfg, ctx.genai_client)

    seen = {obj['label'] for obj in found}
    not_found = [name for name in objects if name not in seen]

    result = {
        'ok': True,
        'status': 'succeeded',
        'objects': found,
        'not_found': not_found,
        'robot': ctx.robot.state(),
    }
    ctx.emit('look', {'objects': found, 'not_found': not_found, 'image_id': snapshot.image_id})

    if ctx.cfg['perception'].get('send_annotated_image', True):
        result['_image'] = annotated
    return result

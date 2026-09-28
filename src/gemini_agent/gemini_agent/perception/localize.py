"""mask + depth + TF -> base-frame centroid, and image annotation."""

import cv2
import numpy as np


def centroid(mask, snapshot) -> dict:
    """Mean of the object's valid depth pixels, in base-frame meters."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return {'measurable': False, 'reason': 'empty mask'}

    z = snapshot.depth_m[ys, xs]
    valid = z > 0.0  # 0 means no depth reading
    if not np.any(valid):
        return {'measurable': False, 'reason': 'no valid depth on object'}

    xs, ys, z = xs[valid], ys[valid], z[valid]

    # deproject with the camera intrinsics
    fx, fy, cx, cy = snapshot.K
    points = np.stack([(xs - cx) * z / fx, (ys - cy) * z / fy, z], axis=1)

    # straight into the projection frame, no axis remap (matches the old measure())
    homogeneous = np.hstack([points, np.ones((len(points), 1))])
    base_points = (snapshot.T_base_proj @ homogeneous.T).T[:, :3]

    mean = base_points.mean(axis=0)
    return {'measurable': True, 'centroid_xyz': [round(float(v), 3) for v in mean]}


def annotate(rgb, objects: list, masks: list):
    """Draw each mask outline and a small id tag, without covering the objects."""
    out = rgb.copy()
    for obj, mask in zip(objects, masks):
        if mask is None:
            continue
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, (0, 255, 0), 2)

        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            continue
        tag_x, tag_y = int(xs.min()), max(int(ys.min()) - 6, 12)  # above the top-left corner
        cv2.putText(out, obj['id'], (tag_x, tag_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(out, obj['id'], (tag_x, tag_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return out

"""ArUco detection: a marker id and a snapshot in, a base-frame point out.

Used by the holster skills to find the tool holder. Points are deprojected and
transformed exactly the way `localize.measure` does, so they are directly
comparable with `look` results.
"""

import cv2
import numpy as np

DICTIONARY = cv2.aruco.DICT_4X4_50

_detector = None


def _get_detector():
    """One detector for the process; constructing it per call is wasteful."""
    global _detector
    if _detector is None:
        _detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(DICTIONARY),
            cv2.aruco.DetectorParameters())
    return _detector


def detect(rgb, marker_id: int):
    """-> (pixel corners 4x2 TL/TR/BR/BL for `marker_id` or None, all ids seen)."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    corners, ids, _ = _get_detector().detectMarkers(gray)
    if ids is None:
        return None, []
    seen = [int(i) for i in ids.flatten()]
    for c, i in zip(corners, seen):
        if i == marker_id:
            return c.reshape(4, 2), seen
    return None, seen


def center_cam(corners, snapshot):
    """Marker center in the projection frame, from the median depth over the marker."""
    mask = np.zeros(snapshot.depth_m.shape, np.uint8)
    cv2.fillConvexPoly(mask, corners.astype(np.int32), 1)
    z = snapshot.depth_m[mask.astype(bool)]
    z = z[z > 0.0]  # 0 means no depth reading
    if len(z) == 0:
        return None
    u, v = corners.mean(axis=0)
    fx, fy, cx, cy = snapshot.K
    d = float(np.median(z))
    return np.array([(u - cx) * d / fx, (v - cy) * d / fy, d])


def to_base(point_cam, snapshot):
    """Projection-frame point -> base-frame point."""
    return (snapshot.T_base_proj @ np.append(point_cam, 1.0))[:3]


def locate(snapshot, marker_id: int, offset_cam=(0.0, 0.0, 0.0)) -> dict:
    """Find `marker_id` and return `marker_center + offset_cam` in the base frame.

    `offset_cam` is in meters in the projection frame (optical convention:
    +x image right, +y image down, +z away from the camera).

    -> {'found': True, 'point_base': (3,), 'marker_base': (3,)} or
       {'found': False, 'reason': str, 'seen': [ids]}
    """
    corners, seen = detect(snapshot.rgb, marker_id)
    if corners is None:
        return {'found': False, 'reason': f'marker {marker_id} not visible', 'seen': seen}

    marker_cam = center_cam(corners, snapshot)
    if marker_cam is None:
        return {'found': False, 'reason': f'marker {marker_id} found but no valid depth on it',
                'seen': seen}

    return {
        'found': True,
        'point_base': to_base(marker_cam + np.array(offset_cam, dtype=float), snapshot),
        'marker_base': to_base(marker_cam, snapshot),
        'seen': seen,
    }

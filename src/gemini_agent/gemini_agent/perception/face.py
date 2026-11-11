"""Face landmarks: a snapshot and a named face region in, a base-frame point out.

MediaPipe Face Landmarker finds the 478 face-mesh landmarks; a region is the average of a
few of them. Points are deprojected and transformed exactly the way `localize.measure` and
`aruco` do, so they are directly comparable with `look` results.

The model file is a separate download (see README) at models/face_landmarker.task.
"""

import cv2
import mediapipe as mp  # import at load time: imported lazily in a worker thread once the
import numpy as np      # camera is subscribed, it hangs (sounddevice's ldconfig call)
from mediapipe.tasks.python import BaseOptions, vision

from gemini_agent.config import REPO_ROOT

# region -> Face Landmarker indices, averaged in pixel space. Left/right are the USER's left
# and right, which appear on the image's right and left in a camera facing them. Check the
# indices with scratch/face_regions.py before trusting a new one; eyes, mouth and nostrils
# are deliberately not regions.
FACE_REGIONS = {
    'middle_forehead': [109, 10, 338, 108, 151, 337],
    'left_forehead': [338, 297, 332, 337, 299, 333],
    'right_forehead': [103, 67, 109, 104, 69, 108],
    'left_cheek': [330, 280, 425],
    'right_cheek': [101, 50, 205],
    'nose': [1, 4, 5],
    'chin': [208, 199, 428, 171, 175, 396],
    'left_eyebrow': [296, 334, 293, 295, 282, 283],
    'right_eyebrow': [66, 105, 63, 53, 52, 65],
    'upper_lip': [167, 164, 393, 37, 0, 267],
    'lower_lip': [84, 17, 314, 83, 18, 313],
    'between_eyebrows':[107, 9, 336, 55, 8, 285],
    'center_of_mouth': [90, 180, 85, 16, 315, 404, 320, 74, 73, 72, 11, 302, 303, 304],

}

MODEL_PATH = REPO_ROOT / 'models' / 'face_landmarker.task'
DEPTH_PATCH_PX = 4   # median depth over a (2n+1)^2 patch around the region's pixel

_landmarker = None


def _get_landmarker():
    """One landmarker for the process, loaded at import (below)."""
    global _landmarker
    if _landmarker is None:
        if not MODEL_PATH.is_file():
            raise FileNotFoundError(f'face landmarker model not found at {MODEL_PATH} (see README)')
        _landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1))
    return _landmarker


# load now (~0.5 s), on the importing thread: loaded later in a worker thread while the camera
# callbacks run, it takes 5+ s. A missing model only fails when a face is first requested.
if MODEL_PATH.is_file():
    _get_landmarker()


def detect(rgb):
    """-> 478x2 landmark pixels (u, v) of the most prominent face, or None."""
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    result = _get_landmarker().detect(image)
    if not result.face_landmarks:
        return None
    h, w = rgb.shape[:2]
    return np.array([(lm.x * w, lm.y * h) for lm in result.face_landmarks[0]])


def region_pixel(landmarks, region: str):
    """Pixel (u, v) of a region: the mean of its landmarks."""
    return landmarks[FACE_REGIONS[region]].mean(axis=0)


def point_cam(snapshot, u: float, v: float):
    """Projection-frame point at pixel (u, v), from the median depth around it, or None."""
    h, w = snapshot.depth_m.shape
    n = DEPTH_PATCH_PX
    ui, vi = int(round(u)), int(round(v))
    patch = snapshot.depth_m[max(0, vi - n):min(h, vi + n + 1), max(0, ui - n):min(w, ui + n + 1)]
    z = patch[patch > 0.0]  # 0 means no depth reading
    if len(z) == 0:
        return None
    fx, fy, cx, cy = snapshot.K
    d = float(np.median(z))
    return np.array([(u - cx) * d / fx, (v - cy) * d / fy, d])


def locate(snapshot, region: str) -> dict:
    """Find the face and return `region` as a base-frame point.

    -> {'found': True, 'point_base': (3,), 'pixel': (u, v), 'landmarks': 478x2} or
       {'found': False, 'reason': str, 'landmarks': 478x2 or None}
    """
    if region not in FACE_REGIONS:
        return {'found': False, 'reason': f'unknown face region {region!r}', 'landmarks': None}

    landmarks = detect(snapshot.rgb)
    if landmarks is None:
        return {'found': False, 'reason': 'no face visible', 'landmarks': None}

    u, v = region_pixel(landmarks, region)
    p_cam = point_cam(snapshot, u, v)
    if p_cam is None:
        return {'found': False, 'reason': f'no valid depth on the {region.replace("_", " ")}',
                'landmarks': landmarks}

    return {
        'found': True,
        'point_base': (snapshot.T_base_proj @ np.append(p_cam, 1.0))[:3],
        'pixel': (float(u), float(v)),
        'landmarks': landmarks,
    }


def annotate(rgb, landmarks, region: str = None):
    """Landmarks as small dots, every region's pixel labeled, `region` highlighted."""
    out = rgb.copy()
    if landmarks is None:
        return out
    for u, v in landmarks:
        cv2.circle(out, (int(u), int(v)), 1, (0, 160, 255), -1)
    for name in FACE_REGIONS:
        u, v = (int(c) for c in region_pixel(landmarks, name))
        color = (0, 255, 0) if name == region else (255, 255, 255)
        cv2.circle(out, (u, v), 6 if name == region else 3, color, -1)
        cv2.putText(out, name, (u + 6, v - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3)
        cv2.putText(out, name, (u + 6, v - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    return out

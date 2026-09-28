"""2D boxes from a non-streaming Gemini call.

Only detect_boxes() is used elsewhere, so this can be swapped for another detector later.
"""

import json
import re

import cv2
from google.genai import types

_PROMPT = (
    'Detect the following in this image: {names}.\n'
    'Return a JSON list. Each entry is {{"label": <one of the requested names, copied exactly>, '
    '"box_2d": [ymin, xmin, ymax, xmax]}} with coordinates normalized to 0-1000.\n'
    'Include one entry per visible instance; several instances may share a label. '
    'Omit any requested object that is not visible. Return [] if none are visible.'
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    return fence.group(1) if fence else text


def detect_boxes(client, cfg: dict, rgb, names: list) -> list:
    """-> [{"label": str, "box_2d": [ymin, xmin, ymax, xmax]}] normalized 0-1000."""
    ok, buf = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError('JPEG encoding failed')

    response = client.models.generate_content(
        model=cfg['detector_model'],
        contents=[
            types.Part.from_bytes(data=buf.tobytes(), mime_type='image/jpeg'),
            _PROMPT.format(names=', '.join(names)),
        ],
        config=types.GenerateContentConfig(
            response_mime_type='application/json',
            thinking_config=types.ThinkingConfig(
                thinking_level=str(cfg['detector_thinking_level']).upper()),
        ),
    )

    try:
        raw = json.loads(_strip_fences(response.text or '[]'))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    boxes = []
    for entry in raw:  # drop anything malformed rather than failing the whole call
        if not isinstance(entry, dict):
            continue
        label = entry.get('label')
        box = entry.get('box_2d')
        if not isinstance(label, str) or not isinstance(box, list) or len(box) != 4:
            continue
        try:
            box = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        boxes.append({'label': label, 'box_2d': box})
    return boxes

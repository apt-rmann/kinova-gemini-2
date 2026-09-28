"""Perception backend for the look tool: names in, base-frame centroids out."""

import asyncio
from collections import defaultdict

import numpy as np

from . import detector, localize, segmenter


async def locate_objects(snapshot, names: list, cfg: dict, client):
    """-> (objects, annotated_rgb). Object ids are valid only for this call."""
    perception_cfg = cfg['perception']
    rgb = snapshot.rgb
    h, w = rgb.shape[:2]

    boxes = await asyncio.to_thread(detector.detect_boxes, client, perception_cfg, rgb, names)

    # normalized 0-1000 (ymin, xmin, ymax, xmax) -> pixel (x1, y1, x2, y2)
    boxes_px = []
    for box in boxes:
        ymin, xmin, ymax, xmax = box['box_2d']
        x1, x2 = sorted((xmin * w / 1000.0, xmax * w / 1000.0))
        y1, y2 = sorted((ymin * h / 1000.0, ymax * h / 1000.0))
        boxes_px.append([x1, y1, x2, y2])

    masks = await asyncio.to_thread(segmenter.segment, perception_cfg, rgb, boxes_px)

    objects = []
    counts = defaultdict(int)
    for box, mask in zip(boxes, masks):
        counts[box['label']] += 1
        obj = {
            'id': f'{box["label"]}#{counts[box["label"]]}',
            'label': box['label'],
        }
        obj.update(localize.centroid(mask, snapshot))
        objects.append(obj)

    annotated = localize.annotate(rgb, objects, masks) if objects else np.copy(rgb)
    return objects, annotated

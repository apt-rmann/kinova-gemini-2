"""SAM2 box-prompted masks, from a local SAM2 checkout."""

import logging
import os
import sys
import time

import numpy as np
import torch

_predictor = None
_log = logging.getLogger('gemini_agent.segmenter')


def _get_predictor(cfg: dict):
    global _predictor
    if _predictor is None:
        # the SAM2 checkout is not pip-installed; its root must be importable so that
        # sam2/__init__.py's initialize_config_module("configs") resolves
        repo = cfg['sam2_repo']
        if repo not in sys.path:
            sys.path.append(repo)
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        ckpt = cfg['sam2_checkpoint']
        if not os.path.isabs(ckpt):
            ckpt = os.path.join(repo, ckpt)

        start = time.time()
        _predictor = SAM2ImagePredictor(
            build_sam2(cfg['sam2_config'], ckpt, device=cfg['device']))
        _log.info('loaded %s on %s in %.1fs', cfg['sam2_config'], cfg['device'], time.time() - start)
    return _predictor


def preload(cfg: dict):
    """Load SAM2 before any camera subscriptions exist.

    Importing torch/torchvision is pure Python and GIL-bound; doing it while the camera
    topics are being deserialized at 30 Hz makes it ~50x slower.
    """
    _get_predictor(cfg)


def segment(cfg: dict, rgb, boxes_px: list) -> list:
    """-> one bool HxW mask per box (the highest-score mask for that box)."""
    if not boxes_px:
        return []

    predictor = _get_predictor(cfg)
    device = cfg['device']

    # SAM2 needs autocast on CUDA; without it scaled_dot_product_attention has no kernel
    autocast = (torch.autocast(device, dtype=torch.bfloat16) if device.startswith('cuda')
                else torch.autocast(device, enabled=False))

    masks = []
    with torch.inference_mode(), autocast:
        predictor.set_image(rgb)
        for box in boxes_px:
            mask, scores, _ = predictor.predict(box=np.array(box, dtype=np.float32),
                                                multimask_output=True)
            best = int(np.argmax(scores))
            masks.append(np.asarray(mask[best]).astype(bool))
    return masks

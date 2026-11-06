"""Check FACE_REGIONS (perception/face.py) on a live camera feed or a photo.

Draws every landmark, labels every region, highlights one, and (live) prints that region's
base-frame point. Regions are the USER's left/right, so `left_cheek` should sit on the
image's right side when the camera faces them.

    python scratch/face_regions.py                          # live, from the wrist camera
    python scratch/face_regions.py --region lower_chin      # highlight + print a region
    python scratch/face_regions.py --indices                # number every landmark (zoom in)
    python scratch/face_regions.py --image face.jpg         # a photo, no robot needed

q or Esc quits; s saves the current frame to scratch/pics/.
"""

import argparse
import asyncio
import sys
import threading
import time

import cv2
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node

from gemini_agent.config import load_config
from gemini_agent.perception import face

PICS = '/home/mcrr-lab/kinova-gemini-2/scratch/pics'


def draw(rgb, landmarks, region, indices):
    out = face.annotate(rgb, landmarks, region)
    if indices and landmarks is not None:
        for i, (u, v) in enumerate(landmarks):
            cv2.putText(out, str(i), (int(u) + 2, int(v)), cv2.FONT_HERSHEY_SIMPLEX, 0.25, (255, 255, 0), 1)
    return out


def show(rgb, wait_ms):
    """-> the key pressed (or -1)."""
    cv2.imshow('face regions', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    key = cv2.waitKey(wait_ms) & 0xFF
    if key == ord('s'):
        path = f'{PICS}/face_{int(time.time())}.png'
        cv2.imwrite(path, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        print(f'saved {path}')
    return key


def from_image(args):
    bgr = cv2.imread(args.image)
    if bgr is None:
        sys.exit(f'could not read {args.image}')
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    landmarks = face.detect(rgb)
    print('no face found' if landmarks is None else f'{len(landmarks)} landmarks')
    while show(draw(rgb, landmarks, args.region, args.indices), 0) not in (ord('q'), 27):
        pass


async def live(camera, args):
    after = camera.now()
    last_print = 0.0
    while True:
        snapshot = await camera.fresh_snapshot(after=after, timeout=2.0)
        after = snapshot.stamp
        found = await asyncio.to_thread(face.locate, snapshot, args.region)
        if time.monotonic() - last_print > 0.5:
            if found['found']:
                print(f'{args.region}: base {np.round(found["point_base"], 3).tolist()}')
            else:
                print(f'{args.region}: {found["reason"]}')
            last_print = time.monotonic()
        if show(draw(snapshot.rgb, found['landmarks'], args.region, args.indices), 1) in (ord('q'), 27):
            return


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--region', default='left_cheek', choices=list(face.FACE_REGIONS))
    p.add_argument('--indices', action='store_true', help='label every landmark with its index')
    p.add_argument('--image', help='run on a photo instead of the camera')
    args = p.parse_args()

    if args.image:
        from_image(args)
        return

    from gemini_agent.camera import Camera   # only the live mode needs ROS

    cfg = load_config()
    rclpy.init()
    node = Node('face_regions')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    try:
        asyncio.run(live(Camera(node, cfg), args))
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        executor.shutdown()
        spin_thread.join(timeout=2.0)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

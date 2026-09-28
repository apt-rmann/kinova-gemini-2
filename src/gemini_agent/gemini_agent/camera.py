"""Synchronized color/depth/intrinsics/TF snapshots from the wrist RealSense."""

import asyncio
import threading
from dataclasses import dataclass

import cv2
import message_filters
import numpy as np
import tf2_ros
from cv_bridge import CvBridge
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image


@dataclass
class Snapshot:
    rgb: np.ndarray          # HxWx3 uint8
    depth_m: np.ndarray      # HxW float32, meters
    K: tuple                 # (fx, fy, cx, cy)
    stamp: float             # seconds
    T_base_proj: np.ndarray  # 4x4, base_frame <- projection_frame
    image_id: str            # "img-0042"


def _quat_to_matrix(x, y, z, w, t):
    """4x4 homogeneous transform from a quaternion and translation."""
    T = np.eye(4)
    T[:3, :3] = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    T[:3, 3] = t
    return T


class Camera:
    def __init__(self, node, cfg):
        self.node = node
        self.cfg = cfg['camera']

        self._bridge = CvBridge()
        self._lock = threading.Lock()
        self._pair = None   # (color_msg, depth_msg, stamp)
        self._K = None
        self._counter = 0

        color_sub = message_filters.Subscriber(node, Image, self.cfg['color_topic'])
        depth_sub = message_filters.Subscriber(node, Image, self.cfg['depth_topic'])
        self._sync = message_filters.ApproximateTimeSynchronizer(
            [color_sub, depth_sub], queue_size=10, slop=self.cfg['sync_slop_s'])
        self._sync.registerCallback(self._on_pair)

        node.create_subscription(CameraInfo, self.cfg['info_topic'], self._on_info, 10)

        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, node)

    # --- subscriptions ---

    def _on_pair(self, color_msg, depth_msg):
        stamp = color_msg.header.stamp.sec + color_msg.header.stamp.nanosec * 1e-9
        with self._lock:
            self._pair = (color_msg, depth_msg, stamp)

    def _on_info(self, msg):
        if self._K is None:
            self._K = (msg.k[0], msg.k[4], msg.k[2], msg.k[5])  # fx, fy, cx, cy

    # --- snapshots ---

    def now(self) -> float:
        return self.node.get_clock().now().nanoseconds * 1e-9

    async def fresh_snapshot(self, after: float = None, timeout: float = 2.0) -> Snapshot:
        """First synchronized color/depth pair stamped after `after` (default: now + settle_s)."""
        if after is None:
            after = self.now() + self.cfg['settle_s']

        deadline = self.now() + max(0.0, after - self.now()) + timeout
        while True:
            with self._lock:
                pair = self._pair
            if pair is not None and pair[2] > after:
                break
            if self.now() > deadline:
                if pair is None:
                    raise TimeoutError(
                        f'no synchronized color/depth pair on {self.cfg["color_topic"]} and '
                        f'{self.cfg["depth_topic"]} - is the camera running?')
                raise TimeoutError(f'no camera frame newer than {after:.3f} within {timeout}s')
            await asyncio.sleep(0.02)

        if self._K is None:
            raise RuntimeError(f'no CameraInfo received on {self.cfg["info_topic"]}')

        await self._await_transform()

        color_msg, depth_msg, stamp = pair
        rgb = self._bridge.imgmsg_to_cv2(color_msg, desired_encoding='rgb8')

        depth = self._bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
        if depth_msg.encoding == '16UC1':
            depth_m = depth.astype(np.float32) * 0.001
        else:
            depth_m = depth.astype(np.float32)

        self._counter += 1
        return Snapshot(
            rgb=rgb,
            depth_m=depth_m,
            K=self._K,
            stamp=stamp,
            T_base_proj=self._lookup_transform(stamp),
            image_id=f'img-{self._counter:04d}',
        )

    async def _await_transform(self, timeout: float = 5.0):
        """Returns immediately once TF knows both frames; /tf_static takes ~3s to arrive."""
        base = self.cfg['base_frame']
        proj = self.cfg['projection_frame']
        deadline = self.now() + timeout
        while not self.tf_buffer.can_transform(base, proj, Time()):
            if self.now() > deadline:
                raise RuntimeError(f'no transform {base} <- {proj} after {timeout}s')
            await asyncio.sleep(0.05)

    def _lookup_transform(self, stamp: float) -> np.ndarray:
        """base_frame <- projection_frame at `stamp`, falling back to the latest transform."""
        base = self.cfg['base_frame']
        proj = self.cfg['projection_frame']
        try:
            tf = self.tf_buffer.lookup_transform(base, proj, Time(nanoseconds=int(stamp * 1e9)))
        except Exception:
            tf = self.tf_buffer.lookup_transform(base, proj, Time())

        t = tf.transform.translation
        r = tf.transform.rotation
        return _quat_to_matrix(r.x, r.y, r.z, r.w, (t.x, t.y, t.z))

    # --- encoding ---

    def encode_jpeg(self, rgb: np.ndarray) -> bytes:
        """Resize to send_width (keeping aspect ratio) and JPEG-encode."""
        h, w = rgb.shape[:2]
        width = int(self.cfg['send_width'])
        if w != width:
            rgb = cv2.resize(rgb, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                               [int(cv2.IMWRITE_JPEG_QUALITY), int(self.cfg['jpeg_quality'])])
        if not ok:
            raise RuntimeError('JPEG encoding failed')
        return buf.tobytes()

"""Entry point: ROS I/O on a background executor thread, asyncio on the main thread."""

import asyncio
import json
import os
import threading
import time

import rclpy
from google import genai
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Empty, String

from gemini_agent import tools as tool_registry
from gemini_agent.camera import Camera
from gemini_agent.config import load_config
from gemini_agent.robot import Robot
from gemini_agent.session import Session
from gemini_agent.tools import ToolContext


class AgentNode(Node):
    def __init__(self, cfg):
        super().__init__('gemini_agent')
        ros = cfg['ros']
        self.events_pub = self.create_publisher(String, ros['events_topic'], 10)
        self.image_pub = self.create_publisher(CompressedImage, ros['image_topic'], 10)

    def emit(self, event_type: str, data: dict):
        event = {'t': time.time(), 'type': event_type, 'data': data}
        self.events_pub.publish(String(data=json.dumps(event, default=str)))
        self.get_logger().info(f'{event_type}: {json.dumps(data, default=str)[:300]}')

    def publish_image(self, jpeg_bytes: bytes, image_id: str, reason: str):
        msg = CompressedImage()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = image_id   # the UI matches images to events by this
        msg.format = 'jpeg'
        msg.data = jpeg_bytes
        self.image_pub.publish(msg)
        self.emit('image', {'id': image_id, 'reason': reason})


def main():
    cfg = load_config()

    # load SAM2 before any camera subscriptions exist: once they are up, this
    # pure-Python import is GIL-starved and takes minutes instead of seconds
    if 'look' in cfg['tools']:
        from gemini_agent.perception import segmenter
        print('loading SAM2...', flush=True)
        segmenter.preload(cfg['perception'])

    rclpy.init()
    node = AgentNode(cfg)

    executor = MultiThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    robot = Robot(node, cfg)
    camera = Camera(node, cfg)

    missing = robot.wait_for_servers(5.0)
    if missing:
        node.get_logger().warn(f'action servers not available: {missing}')
    if not robot.wait_for_state(2.0):
        node.get_logger().warn('no RobotState received - is the controller running?')

    ctx = ToolContext(
        robot=robot, camera=camera, cfg=cfg,
        emit=node.emit, publish_image=node.publish_image,
        genai_client=genai.Client(api_key=os.environ['GEMINI_API_KEY']))
    session = Session(cfg, tool_registry.load(cfg['tools'], cfg), ctx)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ROS callbacks never touch asyncio objects directly
    ros = cfg['ros']
    node.create_subscription(
        String, ros['instruction_topic'],
        lambda msg: loop.call_soon_threadsafe(session.instructions.put_nowait, msg.data), 10)
    node.create_subscription(
        Empty, ros['stop_topic'],
        lambda _msg: loop.call_soon_threadsafe(session.on_stop), 10)

    node.get_logger().info('gemini_agent ready - open web/index.html in a browser')

    try:
        loop.run_until_complete(session.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(session.cancel_tool('shutting down'))
        loop.close()
        executor.shutdown()
        spin_thread.join(timeout=2.0)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

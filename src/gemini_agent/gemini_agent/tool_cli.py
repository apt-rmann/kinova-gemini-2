"""Call any tool from the terminal, without the model.

    ros2 run gemini_agent tool_cli --list
    ros2 run gemini_agent tool_cli move_relative '{"dz": 0.03}'
"""

import argparse
import asyncio
import inspect
import json
import os
import signal
import sys
import threading
import time

import rclpy
from google import genai
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from gemini_agent import tools as tool_registry
from gemini_agent.camera import Camera
from gemini_agent.config import load_config
from gemini_agent.robot import Robot
from gemini_agent.tools import ToolContext


def _emit(event_type: str, data: dict):
    print(f'[{event_type}] {json.dumps(data, default=str)}', file=sys.stderr)


def _publish_image(jpeg_bytes, image_id, reason):
    _emit('image', {'id': image_id, 'reason': reason, 'bytes': len(jpeg_bytes)})


async def _run_one(ctx, fn, args: dict, tool_name: str):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
    except NotImplementedError:
        pass

    # check the arguments before calling, so a TypeError from inside the tool is not
    # mistaken for a bad-argument error
    try:
        inspect.signature(fn).bind(ctx, **args)
    except TypeError as e:
        print(json.dumps({'ok': False, 'status': 'error', 'message': f'bad arguments: {e}'}, indent=2))
        return

    try:
        result = await fn(ctx, **args)
    except asyncio.CancelledError:
        # the robot goal has already been canceled on the way out
        print('\ncanceled (Ctrl-C) - the robot goal was canceled', file=sys.stderr)
        return
    except Exception as e:
        result = {'ok': False, 'status': 'error', 'message': f'{type(e).__name__}: {e}',
                  'robot': ctx.robot.state()}

    image = result.pop('_image', None)
    print(json.dumps(result, indent=2, default=str))

    if image is not None:
        import cv2  # only needed when a tool returns an image
        path = f'/tmp/{tool_name}_{int(time.time())}.jpg'
        cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        print(f'saved image to {path}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(prog='tool_cli', description='run one agent tool')
    parser.add_argument('tool', nargs='?', help='tool name')
    parser.add_argument('args', nargs='?', default='{}', help='JSON object of arguments')
    parser.add_argument('--list', action='store_true', help='print the enabled tool declarations')
    parsed = parser.parse_args()

    cfg = load_config()
    enabled = tool_registry.load(cfg['tools'], cfg)

    if parsed.list:
        print(json.dumps([t['declaration'] for t in enabled.values()], indent=2))
        return

    if not parsed.tool:
        parser.error('a tool name is required (or use --list)')
    if parsed.tool not in enabled:
        parser.error(f'{parsed.tool!r} is not enabled; enabled: {list(enabled)}')

    try:
        args = json.loads(parsed.args)
    except json.JSONDecodeError as e:
        parser.error(f'arguments are not valid JSON: {e}')
    if not isinstance(args, dict):
        parser.error('arguments must be a JSON object')

    if parsed.tool == 'look':
        # load SAM2 first: once the camera is subscribed, this import is GIL-starved
        from gemini_agent.perception import segmenter
        print('loading SAM2...', file=sys.stderr)
        segmenter.preload(cfg['perception'])

    rclpy.init()
    node = Node('tool_cli')
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    try:
        robot = Robot(node, cfg)
        missing = robot.wait_for_servers(5.0)
        if missing:
            node.get_logger().warn(f'action servers not available: {missing}')
        if not robot.wait_for_state(2.0):
            node.get_logger().warn('no RobotState received - is the controller running?')

        ctx = ToolContext(robot=robot, camera=Camera(node, cfg), cfg=cfg,
                          emit=_emit, publish_image=_publish_image,
                          genai_client=genai.Client(api_key=os.environ['GEMINI_API_KEY']))

        asyncio.run(_run_one(ctx, enabled[parsed.tool]['fn'], args, parsed.tool))
    finally:
        executor.shutdown()
        spin_thread.join(timeout=2.0)  # let spin() return before the node goes away
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

import asyncio
import threading
import traceback
import json

from .ble_link import BleCentralLink, dbg_ble
from .stack_launcher import StackLauncher

CONFIG_PREFIX = "CONFIG "

class BLEInterfaceNode(Node):
    """
    Tablet does speech-to-text and sends a plain text instruction over BLE. This node
    receives the text and republishes to user_instructions (ala text_interface_node)

    Also forwards brain status strings back to the tablet

    """

    def __init__(self):
        super().__init__('ble_interface_node')
        self.publisher = self.create_publisher(String, '/user_instructions', 10)
        self.config_publisher = self.create_publisher(String, '/config_update', 10)
        self.create_subscription(String, '/agent/events', self.status_callback, 10)
        self.launcher = StackLauncher(self, self.send_status_line)
        self._loop = None
        self._link = BleCentralLink(self.handle_line, on_connected=self._on_connected)
        self._thread = threading.Thread(target = self._run_ble, daemon = True)
        self._thread.start()
        self.get_logger().info("BLE Node started scanning for phone")

    def _run_ble(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._link.run())
        except Exception:
            dbg_ble("Fatal: BLE thread crashed: \n" + traceback.format_exc())

    def _on_connected(self):
        self.get_logger().info('Phone BLE connected')
        self.launcher.ensure_running()

    def handle_line(self, line):
        if line.startswith(CONFIG_PREFIX):
            payload = line[len(CONFIG_PREFIX):].strip()
            self.config_publisher.publish(String(data=payload))
            dbg_ble(f'Published to /config_update: "{payload}"')
        else:
            self.publish_instruction(line)

    def publish_instruction(self, text):
        msg = String()
        msg.data = text
        self.publisher.publish(msg)
        dbg_ble(f'Published to /user_instructions: "{text}"')
        self.get_logger().info(f'BLE instruction from phone: "{text}"')

    def status_callback(self, msg):
        ev = json.loads(msg.data)
        if ev['type'] == 'model_text':
            self.send_status_line(ev['data']['text'])
        elif ev['type'] == 'model_thought':
            self.send_status_line('thinking: ' + ev['data']['text'])
        elif ev['type'] == 'config':
            self.send_status_line(json.dumps(ev['data'], default=str))
        elif ev['type'] == 'error':
            self.send_status_line('error: ' + str(ev['data'].get('message', ev['data'])))

    def send_status_line(self, text):
        """Send one status message to the phone (framed and chunked by the BLE link)."""
        if self._loop is None:
            dbg_ble("   dropped: BLE loop not running yet")
            return
        dbg_ble(f'-> phone: "{text}"')
        asyncio.run_coroutine_threadsafe(self._link.send_status(text), self._loop)

    def stop_link(self):
        self._link.stop()


def main(args=None):
    rclpy.init(args=args)
    node = BLEInterfaceNode()
    try:
        rclpy.spin(node)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        dbg_ble("Interrupted by user")
        pass
    finally:
        node.launcher.shutdown()
        node.stop_link()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import socket
import struct
import threading
import json
import time
import collections

LISTEN_PORT = 9100
CONFIG_PREFIX = "CONFIG "

class NetworkInterfaceNode(Node):
    """
    Tablet does speech-to-text and sends a plain text instruction over TCP socket. This node
    receives the text and republishes to user_instructions (ala text_interface_node)

    Also forwards brain status strings back to the tablet

    Tablet and kit must be on the same Wi-Fi network
    """

    def __init__(self):
        super().__init__('network_interface_node')
        self.publisher = self.create_publisher(String, '/user_instructions', 10)
        self.config_publisher = self.create_publisher(String, '/config_update', 10)
        self.create_subscription(String, '/agent/events', self.status_callback, 10)

        self.client_conn = None
        self.client_lock = threading.Lock()

        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()

        self._out_cv = threading.Condition()
        self._text_q = collections.deque(maxlen=200)
        self.sender_thread = threading.Thread(target=self.send_loop, daemon=True)
        self.sender_thread.start()

        self.get_logger().info(f'Network interface node listening on TCP port {LISTEN_PORT}')

    def run_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", LISTEN_PORT))
        server.listen(1)

        while True:
            self.get_logger().info("Waiting for the app connection...")
            conn, addr = server.accept()
            self.get_logger().info(f'Tablet connected: {addr}')

            with self.client_lock:
                self.client_conn = conn

            buf = b""
            try:
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, _, buf = buf.partition(b"\n")
                        instruction = line.decode("utf-8", errors="replace").strip()
                        if instruction:
                            self.handle_line(instruction)
            except OSError as e:
                self.get_logger().warn(f'Connection ended: {e}')
            finally:
                with self.client_lock:
                    self.client_conn = None
                try:
                    conn.close()
                except Exception:
                    pass

    def send_loop(self):
        while True:
            with self._out_cv:
                while not self._text_q:
                    self._out_cv.wait()
                packet_type, payload, timeData = self._text_q.popleft()
            self.send_packet(packet_type, payload, timeData)

    def send_packet(self, packet_type: bytes, payload: bytes, timeData):
        with self.client_lock:
            conn = self.client_conn
        if conn is not None:
            t_start = time.time()
            try:
                conn.sendall(packet_type + struct.pack('!I', len(payload)) + payload)
            except OSError as e:
                self.get_logger().warn(f'Failed to send to tablet: {e}')
            t_done = time.time()
            self.get_logger().info(
                f'time before received by node: {(timeData['time_node_rec'] - timeData['time_ev']) * 1000:.0f}ms \n'
                f'time in queue: {(t_start - timeData['time_node_rec']) * 1000:.0f}ms \n'
                f'time to send over wifi: {(t_done - t_start) * 1000:.0f}ms \n'
                f'total time: {(t_done - timeData['time_ev']) * 1000:.0f}ms'
            )

    def status_callback(self, msg):
        ev = json.loads(msg.data)
        t_recv = time.time()
        timeData = {'time_ev': ev['t'], 'time_node_rec': t_recv}
        if ev['type'] == 'model_text':
            self.queue_message(b'\x02', (ev['data']['text'] + "\n").encode("utf-8"), timeData)
        elif ev['type'] == 'model_thought':
            self.queue_message(b'\x02', ('thinking: ' + ev['data']['text'] + "\n").encode("utf-8"), timeData)
        elif ev['type'] == 'config':
            self.queue_message(b'\x02', (json.dumps(ev['data'], default=str) + "\n").encode("utf-8"), timeData)

    def queue_message(self, packet_type, payload, timeData):
        with self._out_cv:
            self._text_q.append((packet_type, payload, timeData))
            self._out_cv.notify()

    def handle_line(self, line):
        if line.startswith(CONFIG_PREFIX):
            payload = line[len(CONFIG_PREFIX):].strip()
            self.config_publisher.publish(String(data=payload))
        else:
            self.publish_instruction(line)

    def publish_instruction(self, instruction):
        msg = String()
        msg.data = instruction
        self.publisher.publish(msg)
        self.get_logger().info(f'Published instruction from tablet: {instruction}')

def main(args=None):
    rclpy.init(args=args)
    node = NetworkInterfaceNode()
    try:
        rclpy.spin(node)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
if __name__ == '__main__':
    main()

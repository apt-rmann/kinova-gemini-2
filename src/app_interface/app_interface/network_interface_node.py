import rclpy
from rclpy.node import Node
from std_msgs.msg import String 
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import threading
import socket
import struct
import threading

LISTEN_PORT = 9100

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
        self.create_subscription(String, '/agent/events', self.status_callback, 10)
        self.create_subscription(Image, '/agent/model_image', self.image_callback, 10)
        self.get_logger().info('Network Interface Node listening...')

        self._bridge = CvBridge()
        self.client_conn = None
        self.client_lock = threading.Lock()

        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()

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
                            self.publish_instruction(instruction)
            except OSError as e: 
                self.get_logger().warn(f'Connection ended: {e}')
            finally:
                with self.client_lock:
                    self.client_conn = None
                try:
                    conn.close()
                except Exception:
                    pass

    def status_callback(self, msg):
        if msg.type == 'model_text': 
            self.send_to_tablet(msg.data)

    def image_callback(self, img):
        compressedImage = self._bridge.cv2_to_compressed_imgmsg(img, dst_format='jpeg')
        jpeg_bytes = compressedImage.data.tobytes()
        self.send_image_to_tablet(jpeg_bytes)

    def send_image_to_tablet(self, imgBytes):
        with self.client_lock:
            conn = self.client_conn
        if conn is not None: 
            try: 
                conn.sendall(b'\x01') # Video Packet Type Indicator
                conn.sendall(struct.pack('!I', len(imgBytes))) # 4-byte Big-Endian Length
                conn.sendall(imgBytes) # Raw JPEG payload content
            except OSError as e:
                self.get_logger().warn(f'Failed to send status to tablet: {e}')

    def send_to_tablet(self, text):
        with self.client_lock:
            conn = self.client_conn
        if conn is not None: 
            try: 
                message_bytes = (text + "\n").encode("utf-8")
                conn.sendall(b'\x02')
                conn.sendall(struct.pack('!I', len(message_bytes)))
                conn.sendall(message_bytes)
            except OSError as e:
                self.get_logger().warn(f'Failed to send status to tablet: {e}')

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
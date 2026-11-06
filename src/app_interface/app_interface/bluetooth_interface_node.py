import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import ctypes
import struct
import os
import socket
import threading
import json
import time
import collections

RFCOMM_CHANNEL = 1

# Anaconda's CPython is built without <bluetooth/bluetooth.h>, so its socket module has no
# AF_BLUETOOTH and cannot parse an RFCOMM address tuple. The kernel (BlueZ) supports RFCOMM
# regardless, so we skip the missing Python layer and call libc directly for the three
# address-taking syscalls. Everything after accept() -- recv/sendall/close -- is
# address-agnostic and works on a plain socket object on any interpreter.
AF_BLUETOOTH = 31
BTPROTO_RFCOMM = 3

CONFIG_PREFIX = "CONFIG "

_libc = ctypes.CDLL("libc.so.6", use_errno=True)

class _SockaddrRC(ctypes.Structure):
    """struct sockaddr_rc from <bluetooth/rfcomm.h>."""
    _fields_ = [
        ("rc_family", ctypes.c_ushort),
        ("rc_bdaddr", ctypes.c_ubyte * 6),
        ("rc_channel", ctypes.c_ubyte),
    ]


def _check(ret, what):
    if ret < 0:
        err = ctypes.get_errno()
        raise OSError(err, f'{what}: {os.strerror(err)}')
    return ret


def rfcomm_listen(channel, backlog=1):
    """Bind an RFCOMM listening socket on `channel` for any local adapter (BDADDR_ANY)."""
    server = socket.socket(AF_BLUETOOTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        addr = _SockaddrRC(rc_family=AF_BLUETOOTH, rc_channel=channel)  # bdaddr zeroed = ANY
        _check(_libc.bind(server.fileno(), ctypes.byref(addr), ctypes.sizeof(addr)), 'bind')
        _check(_libc.listen(server.fileno(), backlog), 'listen')
    except Exception:
        server.close()
        raise
    return server


def rfcomm_accept(server):
    """Block until a peer connects; return (conn_socket, 'AA:BB:..' peer address)."""
    addr = _SockaddrRC()
    length = ctypes.c_uint(ctypes.sizeof(addr))
    # CDLL releases the GIL for the duration of the call, so this blocks the thread, not rclpy.
    fd = _check(_libc.accept(server.fileno(), ctypes.byref(addr), ctypes.byref(length)), 'accept')
    # BlueZ stores bdaddr least-significant byte first, so reverse it for display.
    peer = ':'.join(f'{b:02X}' for b in reversed(bytes(addr.rc_bdaddr)))
    return socket.socket(fileno=fd), peer

def find_free_channel(start=1, end=30):
        errors = []
        for ch in range(start, end + 1):
            try:
                return rfcomm_listen(ch), ch
            except OSError as e:
                errors.append(f'rfcomm ch {ch} OSError: {e}')
        raise OSError("No free RFCOMM channel, checked 1-30: \n" + "\n".join(errors))

class BluetoothInterfaceNode(Node):
    """
    Tablet does speech-to-text and sends a plain text instruction over bluetooth. This node 
    receives the text and republishes to user_instructions (ala text_interface_node)
    
    Also forwards brain status strings back to the tablet

    """

    def __init__(self):
        super().__init__('bluetooth_interface_node')
        self.publisher = self.create_publisher(String, '/user_instructions', 10)
        self.config_publisher = self.create_publisher(String, '/config_update', 10)
        self.create_subscription(String, '/agent/events', self.status_callback, 10)

        self.client_conn = None
        self.client_lock = threading.Lock()
        self.server = None

        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()

        self._out_cv = threading.Condition()
        self._text_q = collections.deque(maxlen=200)
        self.sender_thread = threading.Thread(target=self.send_loop, daemon=True)
        self.sender_thread.start()

        self.get_logger().info(f'Bluetooth interface node listening on RFCOMM ch {RFCOMM_CHANNEL}')


    def run_server(self):

        try:
            server, ch = find_free_channel()
            self.get_logger().info(f'First available ch: ({ch})')
        except OSError as e:
            self.get_logger().error(f'Cannot make BT socket ({e})')
            return

        self.server = server

        while True:
            self.get_logger().info("Waiting for the app BT connection...")
            try:
                conn, addr = rfcomm_accept(server)
            except OSError as e:
                self.get_logger().error(f'BT accept failed, stopping server ({e})')
                return
            self.get_logger().info(f'Tablet BT connected: {addr}')

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
                if self._text_q:
                    packet_type, payload, timeData = self._text_q.popleft()
            self.send_packet(packet_type, payload, timeData)

    def send_packet(self, packet_type: bytes, payload: bytes, timeData):
        with self.client_lock:
            conn = self.client_conn
        if conn is not None:
            t_start_bt = time.time()
            try:
                conn.sendall(packet_type + struct.pack('!I', len(payload)) + payload)
            except OSError as e:
                self.get_logger().warn(f'Failed to send to tablet: {e}')
            t_done_bt = time.time()
            self.get_logger().info(
                f'time before received by node: {(timeData['time_node_rec'] - timeData['time_ev']) * 1000:.0f}ms \n'
                f'time in queue: {(t_start_bt - timeData['time_node_rec']) * 1000:.0f}ms \n'
                f'time to send over bt: {(t_done_bt - t_start_bt) * 1000:.0f}ms \n'
                f'total time: {(t_done_bt - timeData['time_ev']) * 1000:.0f}ms'
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
        self.get_logger().info(f'Published BT instruction from tablet: {instruction}')

def main(args=None):
    rclpy.init(args=args)
    node = BluetoothInterfaceNode()
    try:
        rclpy.spin(node)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        # Release the RFCOMM channel promptly so an immediate relaunch can rebind it.
        for sock in (node.client_conn, node.server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
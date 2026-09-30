import socket
import time
import struct
import threading
import io
from PIL import Image, ImageDraw

LISTEN_PORT = 9100

class MockKitServer:
    def __init__(self):
        self.client_conn = None
        self.client_lock = threading.Lock()

    def run_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", LISTEN_PORT))
        server.listen(1)

        while True:
            print('Waiting for a tablet connection...')
            conn, addr = server.accept()
            print(f'Tablet connected: {addr}')
           
            with self.client_lock:
                self.client_conn = conn

            video_thread = threading.Thread(target=self._video_stream_worker, args=(conn,), daemon=True)
            video_thread.start()

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
                print(f'Connection ended in listener loop: {e}')
            finally:
                with self.client_lock:
                    self.client_conn = None
                try:
                    conn.close()
                except Exception:
                    pass
                print("Tablet disconnected.")

    def _video_stream_worker(self, conn):
        """Generates dynamic binary JPEG images and sends them over the socket."""
        frame_count = 0
        
        try:
            while True:
                with self.client_lock:
                    if self.client_conn is None:
                        break

                # 1. Create a true raster image buffer using PIL (640x480 canvas)
                img = Image.new('RGB', (640, 480), color='#1a1a1a')
                draw = ImageDraw.Draw(img)
               
                # Draw the moving red circle animation sequence
                x_offset = (frame_count % 30) * 10
                draw.ellipse([300 + x_offset, 200, 380 + x_offset, 280], fill='#ff4444')
               
                # 2. Compress the drawing directly into native JPEG binary bytes
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='JPEG')
                frame_bytes = img_byte_arr.getvalue()

                # 3. Stream framing sequence down the raw socket connection
                conn.sendall(b'\x01') # Video Packet Type Indicator
                conn.sendall(struct.pack('!I', len(frame_bytes))) # 4-byte Big-Endian Length
                conn.sendall(frame_bytes) # Raw JPEG payload content
               
                frame_count += 1
                time.sleep(0.05) # ~20 FPS

        except (OSError, BrokenPipeError) as e:
            print(f"Video stream worker stopped: {e}")

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
                print(f'Failed to send status to tablet: {e}')

    def publish_instruction(self, instruction):
        print(f'>>> RECEIVED FROM TABLET: "{instruction}"')
        self.send_to_tablet(f'Mock kit received: {instruction}')

if __name__ == '__main__':
    server = MockKitServer()
    server.run_server()
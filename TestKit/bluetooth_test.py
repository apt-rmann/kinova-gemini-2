
import asyncio
import ctypes
import os
import io
import socket
import threading
import struct
import time
from PIL import Image, ImageDraw

RFCOMM_CHANNEL = 4

# Anaconda's CPython is built without <bluetooth/bluetooth.h>, so its socket module has no
# AF_BLUETOOTH and cannot parse an RFCOMM address tuple. The kernel (BlueZ) supports RFCOMM
# regardless, so we skip the missing Python layer and call libc directly for the three
# address-taking syscalls. Everything after accept() -- recv/sendall/close -- is
# address-agnostic and works on a plain socket object on any interpreter.
AF_BLUETOOTH = 31
BTPROTO_RFCOMM = 3

import socket

def rfcomm_listen(channel=1, backlog=1):
    """Bind an RFCOMM listening socket natively on Windows/Linux."""
    server = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    
    try:
        server.bind((socket.BDADDR_ANY, channel))
        
        server.listen(backlog)
        print(f"Listening for Bluetooth connections on RFCOMM channel {channel}...")
        
    except Exception:
        server.close()
        raise
        
    return server

def find_free_channel(start=1, end=30):
        errors = []
        for ch in range(start, end + 1):
            try:
                return rfcomm_listen(ch), ch
            except OSError as e:
                errors.append(f'rfcomm ch {ch} OSError: {e}')
        raise OSError("No free RFCOMM channel, checked 1-30: \n" + "\n".join(errors))

def rfcomm_accept(server):

    print("Waiting for an incoming Android connection...")
    
    conn_socket, client_info = server.accept()
    peer_address = client_info[0] 
    
    print(f"Connection accepted from remote device: {peer_address}")
    return conn_socket, peer_address

def send_test_images(conn, addr, stop_event):
    frame_count = 0
    while not stop_event.is_set():
        try:
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
            header = struct.pack('!I', len(frame_bytes))
            conn.sendall(header) # 4-byte Big-Endian Length
            conn.sendall(frame_bytes) # Raw JPEG payload content

            frame_count += 1
            print(f"Sent image w/ header: {header}")
            time.sleep(0.1) # ~10 FPS

        except OSError as e:
            print(f"Video stream stopped: {e}")

        

def handle_client_connection(conn, addr):

    buf = b""
    disconnect_event = threading.Event()

    sender_thread = threading.Thread(
        target=send_test_images, 
        args=(conn, addr, disconnect_event),
        daemon=True
    )
    sender_thread.start()

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
                    publish_instruction(instruction)
                    response_str = f"Acknowledged: {instruction}\n"
                    message_bytes = (response_str + "\n").encode("utf-8")
                    conn.sendall(b'\x02')
                    conn.sendall(struct.pack('!I', len(message_bytes)))
                    conn.sendall(message_bytes)
    except OSError as e:
        print(f'Connection ended: {e}')
    finally:
        disconnect_event.set()
        sender_thread.join(timeout=1.0)
        try:
            conn.close()
        except Exception:
            pass

def run_server():

    try:
        server, ch = find_free_channel()

    except OSError as e:
        print(f'Cannot make BT socket ({e})')
        return

    try:
        while True:
            try:
                conn, addr = rfcomm_accept(server)
                handle_client_connection(conn, addr)
            except OSError as e:
                print(f'BT accept failed, stopping server ({e})')
                break

    finally:
        server.close()


def publish_instruction(instruction):
    print(f'Published BT instruction from tablet: {instruction}')

async def main():
    await asyncio.to_thread(run_server())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
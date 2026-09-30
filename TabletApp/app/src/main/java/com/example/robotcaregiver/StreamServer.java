package com.example.robotcaregiver;

import static fi.iki.elonen.NanoHTTPD.MIME_PLAINTEXT;

import java.io.DataInputStream;
import java.io.InputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;

import fi.iki.elonen.NanoHTTPD;

public class StreamServer extends NanoHTTPD {

    private final DataInputStream protocolReader;
    private PipedOutputStream videoPipeOut;
    private PipedInputStream videoPipeIn;
    private final KitMessageListener messageListener;

    public StreamServer(int port, InputStream rawSocketInputStream, KitMessageListener messageListener) throws Exception {

        super("0.0.0.0", port);
        this.protocolReader = new DataInputStream(rawSocketInputStream);
        this.videoPipeOut = new PipedOutputStream();
        this.videoPipeIn = new PipedInputStream(videoPipeOut);
        this.messageListener = messageListener;

        // Start a thread to parse the raw network stream to find video
        startParsingThread();
    }

    private void startParsingThread() {
        Thread t = new Thread(() -> {
            try {
                while (true) {
                    // Read the protocol indicator byte
                    int packetType = protocolReader.read();
                    if (packetType == -1) break; // Socket closed

                    if (packetType == 0x01) {
                        // This is a video frame
                        int frameLength = protocolReader.readInt();
                        byte[] frameBuffer = new byte[frameLength];

                        // Read complete frame payload block
                        protocolReader.readFully(frameBuffer);

                        String frameHeader = "--frame\r\n" +
                                "Content-Type: image/jpeg\r\n" +
                                "Content-Length: " + frameLength + "\r\n\r\n";

                        // Pass image/video bytes into ExoPlayer's input pipe
                        videoPipeOut.write(frameHeader.getBytes("UTF-8"));
                        videoPipeOut.write(frameBuffer);
                        videoPipeOut.write("\r\n".getBytes("UTF-8"));
                        videoPipeOut.flush();
                    } else if (packetType == 0x02) {
                        // Example: A command feedback or status payload from the kit
                        int msgLength = protocolReader.readInt();
                        byte[] msgBytes = new byte[msgLength];
                        protocolReader.readFully(msgBytes);
                        String statusMsg = new String(msgBytes, "UTF-8");
                        if (messageListener != null) {
                            messageListener.onNonVideoStreamReceived(statusMsg);
                        }
                    }
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
        t.setDaemon(true);
        t.start();
    }

    @Override
    public Response serve(IHTTPSession session) {
        String uri = session.getUri();

        if ("/stream".equals(uri)) {
            // Provide ExoPlayer with a clean, continuous stream containing no protocol headers
            Response response = NanoHTTPD.newChunkedResponse(
                    NanoHTTPD.Response.Status.OK,
                    "multipart/x-mixed-replace; boundary=frame",
                    this.videoPipeIn
            );

            response.addHeader("Cache-Control", "no-cache, no-store, must-revalidate");
            response.addHeader("Pragma", "no-cache");
            response.addHeader("Expires", "0");
            response.addHeader("Connection", "keep-alive");
            return response;
        }

        return newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not Found");
    }

    @Override
    public void stop() {
        super.stop();
        try {
            if (videoPipeOut != null) videoPipeOut.close();
            if (videoPipeIn != null) videoPipeIn.close();
        } catch (Exception ignored) {}
    }

    public interface KitMessageListener {
        void onNonVideoStreamReceived(String message);
    }
}
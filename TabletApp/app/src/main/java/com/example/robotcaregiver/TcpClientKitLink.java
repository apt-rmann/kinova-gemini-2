package com.example.robotcaregiver;

import android.content.Context;

import androidx.annotation.NonNull;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

import fi.iki.elonen.NanoHTTPD;

public class TcpClientKitLink implements KitLink{


    private final String host;
    private final int port;
    private KitLink.Listener listener;
    private Socket socket;
    private OutputStream out;

    private StreamServer streamServer;



    public TcpClientKitLink(String host, int port, Context context){
        this.host = host;
        this.port = port;
    }

    public void setListener(KitLink.Listener listener){ this.listener = listener; }

    public void connect() {
        new Thread(() -> {
            try {
                Socket s = new Socket();
                s.connect(new InetSocketAddress(host, port), 8000);
                socket = s;
                out = s.getOutputStream();
                notifyConn(true, "Connected to kit " + host + ":" + port);
                InputStream tcpStream = socket.getInputStream();
                streamServer = new StreamServer(port, tcpStream, new StreamServer.KitMessageListener() {
                    @Override
                    public void onNonVideoStreamReceived(String message) {
                        listener.onKitStatus(message);
                    }
                });
                streamServer.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false);
                setupCamera();
            } catch (Exception e){
                notifyConn(false, "Connection failed: " + e.getMessage());
            }
        }, "TcpClientKitConnection").start();
    }

    private void setupCamera(){

        if (listener != null) {
            // Notify your UI implementation of the loopback HTTP URI
            String localVideoUrl = "http://localhost:" + port + "/stream";
            listener.onKitCamera(localVideoUrl);
        }

    }

    public void sendInstruction(@NonNull String text){
        new Thread(() -> {
            try {
                if (out != null){
                    out.write((text + "\n").getBytes(StandardCharsets.UTF_8));
                    out.flush();
                }
            } catch (Exception e){
                notifyConn(false, "Send failed: " + e.getMessage());
            }
        }, "TcpClientKitSend").start();
    }

    public boolean isConnected() {
        return socket != null && socket.isConnected() && !socket.isClosed();
    }

    public void disconnect() {
        try {
            if (socket != null) socket.close();
            if (streamServer != null) streamServer.stop();
        } catch (Exception e) {}
        notifyConn(false, "Disconnected");
    }

    private void notifyConn(boolean connected, String detail) {
        if(listener != null) listener.onKitConnectionChanged(connected, detail);
    }
}

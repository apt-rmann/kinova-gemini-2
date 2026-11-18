package com.example.robotcaregiver;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * All traffic between the app and the kit.
 *
 * MainActivity creates and connects the KitLink and hands it over to KitMessages w/  setLink.
 * From then on this class is the link's listener
 */
public final class KitMessages {

    public interface Listener {
        // Model output or status text from the kit
        default void onKitStatus(@NonNull String text) {}

        // Config reply from the kit
        default void onConfigReply(@NonNull JSONObject reply) {}

        // The link connected or dropped
        default void onKitConnectionChanged(boolean connected, @NonNull String detail) {}

        default void onKitCamera(@NonNull String streamUrl) {}

        default void onDbgMsg(@NonNull String msg) {}
    }

    private static final String CONFIG_PREFIX = "CONFIG ";
    private static final String STACK_PREFIX = "STACK ";
    private static final String ROBOT_READY = "Robot ready.";

    private static final Handler main = new Handler(Looper.getMainLooper());

    @Nullable private static KitLink link;
    @Nullable private static LinkListener linkListener;
    // config.yaml `tools:` block, {"<tool>": {"marker": id, "length": m}};
    @Nullable private static JSONObject tools;
    // config.yaml `presets:` block, {"<name>": [7 joint angles, degrees]};
    @Nullable private static JSONObject presets;
    private static final List<Listener> listeners = new ArrayList<>();
    private static int nextRequestId = 0;

    private KitMessages() {}

    public static void setLink(@Nullable KitLink newLink) {
        boolean wasConnected = isConnected();
        if (link != null) link.setListener(null);
        link = newLink;
        linkListener = null;
        tools = null;
        presets = null;
        if (newLink != null) {
            linkListener = new LinkListener(newLink);
            newLink.setListener(linkListener);
        }
        if (wasConnected) {
            for (Listener l : new ArrayList<>(listeners)) l.onKitConnectionChanged(false, "");
        }
    }

    // Disconnect the current link without telling the listeners
    public static void disconnectQuietly() {
        if (link == null) return;
        link.setListener(null);
        link.disconnect();
        link.setListener(linkListener);
    }

    public static boolean isConnected() {
        return link != null && link.isConnected();
    }

    // Send a user instruction to the kit
    public static boolean sendInstruction(@NonNull String text) {
        if (!isConnected()) return false;
        link.sendInstruction(text);
        return true;
    }

    // Ask the kit to stop the robot stack it launched
    public static boolean stopRobot() {
        if (!isConnected()) return false;
        link.sendInstruction(STACK_PREFIX + "stop");
        return true;
    }

    // Send a config request
    @Nullable
    public static String send(@NonNull JSONObject request) {
        if (!isConnected()) return null;
        try {
            if (!request.has("id")) request.put("id", "tablet-" + (++nextRequestId));
            link.sendInstruction(CONFIG_PREFIX + request);
            return request.getString("id");
        } catch (JSONException e) {
            Log.w("KitMessages", "couldn't send " + request, e);
            return null;
        }
    }

    // Build a config operation
    @NonNull
    public static JSONObject op(@NonNull String op) {
        try {
            return new JSONObject().put("op", op);
        } catch (JSONException e) {
            throw new IllegalStateException(e);   // only on a null key
        }
    }

    // Ask the kit for its holster and presets
    public static void requestSync() {
        send(op("get_holster"));
        send(op("get_presets"));
    }

    // Copy of the kit's holster block
    @Nullable
    public static JSONObject getTools() {
        return copy(tools);
    }

    // Copy of the kit's presets block
    @Nullable
    public static JSONObject getPresets() {
        return copy(presets);
    }

    public static void addListener(@NonNull Listener l) {
        if (!listeners.contains(l)) listeners.add(l);
    }

    public static void removeListener(@NonNull Listener l) {
        listeners.remove(l);
    }

    // A status line from the kit
    private static void onStatus(@NonNull String status) {
        String text = status.trim();
        if (text.isEmpty()) return;
        JSONObject reply = parseConfigReply(text);
        if (reply != null) {
            JSONObject t = reply.optJSONObject("tools");
            if (t != null) tools = t;
            JSONObject p = reply.optJSONObject("presets");
            if (p != null) presets = p;
            for (Listener l : new ArrayList<>(listeners)) l.onConfigReply(reply);
            return;
        }
        if (text.equals(ROBOT_READY)) requestSync();
        for (Listener l : new ArrayList<>(listeners)) l.onKitStatus(text);
    }

    private static void onConnectionChanged(boolean connected, @NonNull String detail) {
        if (connected) {
            requestSync();   // holster + presets for the config editor
        } else {
            tools = null;    // re-sync on the next connection
            presets = null;
        }
        for (Listener l : new ArrayList<>(listeners)) l.onKitConnectionChanged(connected, detail);
    }

    // The reply if text is a config_editor reply ({"ok", "op" or "tools", ...}), else null. */
    @Nullable
    private static JSONObject parseConfigReply(@NonNull String text) {
        if (!text.startsWith("{")) return null;
        JSONObject reply;
        try {
            reply = new JSONObject(text);
        } catch (JSONException e) {
            return null;
        }
        if (!reply.has("ok") || !(reply.has("op") || reply.has("tools"))) return null;
        return reply;
    }

    @Nullable
    private static JSONObject copy(@Nullable JSONObject o) {
        if (o == null) return null;
        try {
            return new JSONObject(o.toString());
        } catch (JSONException e) {
            return null;
        }
    }


     // Listener installed on the link
    private static final class LinkListener implements KitLink.Listener {
        private final KitLink source;

        LinkListener(KitLink source) {
            this.source = source;
        }

        private void post(Runnable r) {
            main.post(() -> {
                if (link == source) r.run();
            });
        }

        @Override public void onKitStatus(@NonNull String status) {
            post(() -> onStatus(status));
        }

        @Override public void onKitConnectionChanged(boolean connected, @NonNull String detail) {
            post(() -> onConnectionChanged(connected, detail));
        }

        @Override public void onDbgMsg(String msg) {
            post(() -> {
                for (Listener l : new ArrayList<>(listeners)) l.onDbgMsg(msg == null ? "" : msg);
            });
        }

        @Override public void onKitCamera(String streamUrl) {
            if (streamUrl == null) return;
            post(() -> {
                for (Listener l : new ArrayList<>(listeners)) l.onKitCamera(streamUrl);
            });
        }
    }
}

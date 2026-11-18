package com.example.robotcaregiver;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.textfield.MaterialAutoCompleteTextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;


public class RobotActivity extends AppCompatActivity
        implements KitMessages.Listener, DynamicListAdapter.PresetActions {

    // Tool and preset names: start with a letter, then letters, digits or underscores (max 32)
    private static final Pattern NAME = Pattern.compile("^[A-Za-z][A-Za-z0-9_]{0,31}$");
    private static final double MAX_TOOL_LENGTH = 1.0;   // meters
    private static final Set<String> PROTECTED_PRESETS =
            new HashSet<>(Arrays.asList("home", "holster"));
    private static final long REPLY_TIMEOUT_MS = 5000;

    private static final class Slot {
        final String tool;
        final double length;   // meters

        Slot(String tool, double length) {
            this.tool = tool;
            this.length = length;
        }

        boolean sameAs(Slot other) {
            return other != null && tool.equals(other.tool)
                    && Math.abs(length - other.length) < 1e-9;
        }
    }

    private static final List<String> CONFIG_BLOCKS = Arrays.asList("Holster", "Presets");
    private static final int BLOCK_HOLSTER = 0;
    private static final int BLOCK_PRESETS = 1;
    private enum Pending { NONE, FREE_ON, CAPTURE, FREE_OFF }

    private DynamicListAdapter adapter;
    private RecyclerView recyclerView;
    private MaterialAutoCompleteTextView dropdownMenu;
    private Button btnAddItem;
    private Button btnDone;
    private OnBackPressedCallback blockBack;

    private final Map<Integer, Slot> current = new TreeMap<>();
    private final List<String> loadedPresetNames = new ArrayList<>();
    private boolean holsterLoaded;
    private boolean presetsLoaded;

    private Pending pending = Pending.NONE;
    @Nullable private String pendingId;
    @Nullable private PresetItem pendingItem;
    @Nullable private PresetItem freeMoveItem;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable replyTimeout = this::onReplyTimeout;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_robot);

        recyclerView = findViewById(R.id.recyclerView);
        adapter = new DynamicListAdapter();
        adapter.setPresetActions(this);
        recyclerView.setLayoutManager(new LinearLayoutManager(this));
        recyclerView.setAdapter(adapter);

        // Start from what's already on the kit
        loadCurrentHolster(KitMessages.getTools());
        loadCurrentPresets(KitMessages.getPresets());

        // Config block dropdown
        dropdownMenu = findViewById(R.id.dropdownMenu);
        dropdownMenu.setAdapter(new ArrayAdapter<>(
                this, android.R.layout.simple_list_item_1, CONFIG_BLOCKS));
        dropdownMenu.setText(CONFIG_BLOCKS.get(BLOCK_HOLSTER), false);

        // Switch the list's row layout to the chosen block
        dropdownMenu.setOnItemClickListener((parent, view, position, id) -> {
            if (position == BLOCK_HOLSTER) {
                adapter.setListFormat(DynamicListAdapter.VIEW_TYPE_HOLSTER);
            } else if (position == BLOCK_PRESETS) {
                adapter.setListFormat(DynamicListAdapter.VIEW_TYPE_PRESET);
                if (!presetsLoaded) toast("Presets haven't loaded from the kit yet");
            }
        });

        btnAddItem = findViewById(R.id.btnAddItem);
        btnAddItem.setOnClickListener(v -> {
            if (adapter.getListFormat() == DynamicListAdapter.VIEW_TYPE_PRESET && !presetsLoaded) {
                toast("Presets haven't loaded from the kit yet");
                return;
            }
            adapter.addNewItem();
            recyclerView.smoothScrollToPosition(Math.max(0, adapter.getItemCount() - 1));
        });

        btnDone = findViewById(R.id.btnDone);
        btnDone.setOnClickListener(v -> onDone());

        // While the arm is in admittance mode, Back is blocked, capture current position to relock  arm  & unblock back
        blockBack = new OnBackPressedCallback(false) {
            @Override
            public void handleOnBackPressed() {
                toast(freeMoveItem != null
                        ? "Press Capture to lock the arm first"
                        : "Waiting for the kit…");
            }
        };
        getOnBackPressedDispatcher().addCallback(this, blockBack);

        KitMessages.addListener(this);

        // ask for current holster & presets from kit
        if ((!holsterLoaded || !presetsLoaded) && KitMessages.isConnected()) {
            KitMessages.requestSync();
        }
    }

    @Override
    protected void onDestroy() {
        KitMessages.removeListener(this);
        handler.removeCallbacks(replyTimeout);
        super.onDestroy();
    }

    private void loadCurrentHolster(@Nullable JSONObject tools) {
        if (tools == null) return;
        try {
            Iterator<String> names = tools.keys();
            while (names.hasNext()) {
                String name = names.next();
                JSONObject entry = tools.getJSONObject(name);
                current.put(entry.getInt("marker"), new Slot(name, entry.optDouble("length", 0.0)));
            }
        } catch (JSONException e) {
            current.clear();
            toast("Couldn't read the kit's holster: " + e.getMessage());
            return;
        }
        for (Map.Entry<Integer, Slot> e : current.entrySet()) {
            Slot slot = e.getValue();
            adapter.addHolsterItem(String.valueOf(e.getKey()), slot.tool,
                    slot.length == 0 ? "" : formatLength(slot.length));
        }
        holsterLoaded = true;
    }

    private void loadCurrentPresets(@Nullable JSONObject presets) {
        if (presets == null) return;
        Map<String, double[]> parsed = new java.util.LinkedHashMap<>();
        try {
            Iterator<String> names = presets.keys();
            while (names.hasNext()) {
                String name = names.next();
                double[] angles = parseAngles(presets.getJSONArray(name));
                if (angles == null) throw new JSONException(name + " needs 7 joint angles");
                parsed.put(name, angles);
            }
        } catch (JSONException e) {
            toast("Couldn't read the kit's presets: " + e.getMessage());
            return;
        }
        for (Map.Entry<String, double[]> e : parsed.entrySet()) {
            loadedPresetNames.add(e.getKey());
            adapter.addPresetItem(e.getKey(), e.getValue());
        }
        presetsLoaded = true;
    }

    @Override
    public boolean isProtected(PresetItem item) {
        return item.getOriginalName() != null && PROTECTED_PRESETS.contains(item.getOriginalName());
    }

    @Override
    public void onMoveArm(PresetItem item) {
        if (pending != Pending.NONE || freeMoveItem != null) return;
        sendForRow(Pending.FREE_ON, item, freeMoveRequest(true));
    }

    @Override
    public void onCapture(PresetItem item) {
        if (pending != Pending.NONE) return;
        if (freeMoveItem != null && freeMoveItem != item) return;
        sendForRow(Pending.CAPTURE, item, KitMessages.op("get_joint_angles"));
    }

    @Override
    public void onDelete(PresetItem item) {
        if (pending != Pending.NONE || freeMoveItem != null || isProtected(item)) return;
        String name = item.getName() == null ? "" : item.getName().trim();
        new AlertDialog.Builder(this)
                .setTitle("Delete preset?")
                .setMessage(name.isEmpty()
                        ? "Delete this preset?"
                        : "Delete the preset '" + name + "'? It is removed from the kit when you press Done.")
                .setPositiveButton("Delete", (d, w) -> adapter.removePresetItem(item))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void sendForRow(Pending msg, PresetItem item, JSONObject request) {
        String id = KitMessages.send(request);
        if (id == null) {
            toast("Not connected to the kit");
            return;
        }
        pending = msg;
        pendingId = id;
        pendingItem = item;
        handler.removeCallbacks(replyTimeout);
        handler.postDelayed(replyTimeout, REPLY_TIMEOUT_MS);
        updateLockUi();
    }

    private static JSONObject freeMoveRequest(boolean enabled) {
        try {
            return KitMessages.op("set_free_move").put("enabled", enabled);
        } catch (JSONException e) {
            throw new IllegalStateException(e);
        }
    }

    @Override
    public void onConfigReply(@NonNull JSONObject reply) {
        if (!holsterLoaded && reply.has("tools") && adapter.getHolsterItems().isEmpty()) {
            loadCurrentHolster(reply.optJSONObject("tools"));
        }
        if (!presetsLoaded && reply.has("presets") && adapter.getPresetItems().isEmpty()) {
            loadCurrentPresets(reply.optJSONObject("presets"));
        }

        if (pending == Pending.NONE || pendingId == null
                || !pendingId.equals(reply.optString("id"))) return;
        handler.removeCallbacks(replyTimeout);

        Pending msg = pending;
        PresetItem item = pendingItem;
        pending = Pending.NONE;
        pendingId = null;
        pendingItem = null;

        boolean ok = reply.optBoolean("ok", false);
        String message = reply.optString("message", "");

        switch (msg) {
            case FREE_ON:
                if (ok) {
                    freeMoveItem = item;
                    toast("Arm is free. Move it by hand, then press Capture.");
                } else {
                    toast("Couldn't free the arm: " + orUnknown(message));
                }
                break;

            case CAPTURE: {
                double[] angles = ok ? parseAngles(reply.optJSONArray("joint_angles")) : null;
                if (angles == null) {
                    toast("Couldn't read the arm's position: "
                            + (ok ? "reply had no joint angles" : orUnknown(message)));
                    break;
                }
                item.setAngles(angles);
                adapter.refreshPresetItem(item);
                if (freeMoveItem != null) {
                    // Captured while free: lock the arm again
                    sendForRow(Pending.FREE_OFF, item, freeMoveRequest(false));
                }
                break;
            }

            case FREE_OFF:
                if (ok) {
                    freeMoveItem = null;
                } else {
                    toast("Couldn't lock the arm: " + orUnknown(message)
                            + ". Press Capture to try again.");
                }
                break;

            default:
                break;
        }
        updateLockUi();
    }

    private void onReplyTimeout() {
        Pending msg = pending;
        PresetItem item = pendingItem;
        pending = Pending.NONE;
        pendingId = null;
        pendingItem = null;
        switch (msg) {
            case FREE_ON:
                freeMoveItem = item;
                toast("No reply from the kit. Press Capture to lock the arm.");
                break;
            case CAPTURE:
                toast("No reply from the kit; position not captured");
                break;
            case FREE_OFF:
                toast("No reply from the kit. Press Capture to lock the arm.");
                break;
            default:
                break;
        }
        updateLockUi();
    }

    @Override
    public void onKitConnectionChanged(boolean connected, @NonNull String detail) {
        if (connected) return;
        handler.removeCallbacks(replyTimeout);
        boolean wasActive = pending != Pending.NONE || freeMoveItem != null;
        pending = Pending.NONE;
        pendingId = null;
        pendingItem = null;
        freeMoveItem = null;
        updateLockUi();
        if (wasActive) toast("Disconnected from the kit");
    }

    //Free-move or waiting for the kit, only the free row's Capture stays enabled
    private void updateLockUi() {
        boolean locked = freeMoveItem != null || pending != Pending.NONE;
        adapter.setPresetLock(freeMoveItem, pending != Pending.NONE);
        btnAddItem.setEnabled(!locked);
        btnDone.setEnabled(!locked);
        dropdownMenu.setEnabled(!locked);
        blockBack.setEnabled(locked);
    }


    private void onDone() {
        List<String> ops = new ArrayList<>();
        if (!buildHolsterOps(ops)) return;
        if (!buildPresetOps(ops)) return;

        if (ops.isEmpty()) {
            setResult(RESULT_CANCELED);
            finish();
            return;
        }
        if (!KitMessages.isConnected()) {
            toast("Not connected to the kit. Changes not sent.");
            return;
        }
        for (String op : ops) {
            try {
                KitMessages.send(new JSONObject(op));
            } catch (JSONException e) {
                toast("Couldn't build config: " + e.getMessage());
                return;
            }
        }
        setResult(RESULT_OK);
        finish();
    }

    // Validate the holster rows and add the ops that make the kit match them
    private boolean buildHolsterOps(List<String> ops) {
        List<HolsterItem> items = adapter.getHolsterItems();
        Map<Integer, Slot> desired = new TreeMap<>();
        Map<String, Integer> nameToMarker = new HashMap<>();

        for (int i = 0; i < items.size(); i++) {
            HolsterItem item = items.get(i);
            String numberText = item.getNumber() == null ? "" : item.getNumber().trim();
            String tool = item.getTool() == null ? "" : item.getTool().trim();
            String lengthText = item.getLength() == null ? "" : item.getLength().trim();

            int number;
            try {
                number = Integer.parseInt(numberText);
            } catch (NumberFormatException e) {
                return fail("Holster row " + (i + 1) + " needs a marker number");
            }
            if (number <= 0) {
                return fail("Holster row " + (i + 1) + ": marker number must be greater than 0");
            }
            if (tool.isEmpty()) {
                return fail("Marker " + number + " needs a tool name");
            }
            if (!NAME.matcher(tool).matches()) {
                return fail("Marker " + number + ": tool name must start with a letter and contain "
                        + "only letters, numbers, or underscores (max 32)");
            }
            double length = 0.0;
            if (!lengthText.isEmpty()) {
                try {
                    length = Double.parseDouble(lengthText);
                } catch (NumberFormatException e) {
                    length = -1;
                }
                if (!(length >= 0 && length <= MAX_TOOL_LENGTH)) {
                    return fail("Marker " + number + ": length must be in meters, from 0 to "
                            + MAX_TOOL_LENGTH);
                }
            }
            if (desired.containsKey(number)) {
                return fail("Marker " + number + " is used more than once");
            }
            Integer other = nameToMarker.put(tool.toLowerCase(Locale.ROOT), number);
            if (other != null) {
                return fail("'" + tool + "' is in both marker " + other + " and marker " + number);
            }
            desired.put(number, new Slot(tool, length));
        }

        // Clear every removed or changed slot first, then set every changed or new one
        try {
            for (Map.Entry<Integer, Slot> e : current.entrySet()) {
                if (!e.getValue().sameAs(desired.get(e.getKey()))) {
                    ops.add(KitMessages.op("clear_holster_slot")
                            .put("marker_id", e.getKey()).toString());
                }
            }
            for (Map.Entry<Integer, Slot> e : desired.entrySet()) {
                Slot slot = e.getValue();
                if (!slot.sameAs(current.get(e.getKey()))) {
                    ops.add(KitMessages.op("set_holster_slot")
                            .put("marker_id", e.getKey())
                            .put("tool", slot.tool)
                            .put("length", slot.length)
                            .toString());
                }
            }
        } catch (JSONException e) {
            return fail("Couldn't build config: " + e.getMessage());
        }
        return true;
    }

    // Validate the preset rows and add the ops that make the kit match them.
    private boolean buildPresetOps(List<String> ops) {
        if (!presetsLoaded) return true;   // nothing to diff against; presets weren't edited

        List<PresetItem> items = adapter.getPresetItems();
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < items.size(); i++) {
            PresetItem item = items.get(i);
            String name = item.getName() == null ? "" : item.getName().trim();
            if (name.isEmpty()) {
                return fail("Preset row " + (i + 1) + " needs a name");
            }
            if (!NAME.matcher(name).matches()) {
                return fail("Preset '" + name + "': name must start with a letter and contain only "
                        + "letters, numbers, or underscores (max 32)");
            }
            if (!seen.add(name.toLowerCase(Locale.ROOT))) {
                return fail("Preset name '" + name + "' is used more than once");
            }
            if (item.getAngles() == null) {
                return fail("Preset '" + name + "' needs its angles captured");
            }
        }

        try {
            // Delete removed and renamed presets first, then set new or changed ones
            Set<String> kept = new HashSet<>();
            for (PresetItem item : items) {
                String name = item.getName().trim();
                if (name.equals(item.getOriginalName())) kept.add(name);
            }
            for (String name : loadedPresetNames) {
                if (!kept.contains(name)) {
                    ops.add(KitMessages.op("delete_preset").put("name", name).toString());
                }
            }
            for (PresetItem item : items) {
                String name = item.getName().trim();
                double[] angles = item.getAngles();
                boolean unchanged = name.equals(item.getOriginalName())
                        && Arrays.equals(angles, item.getOriginalAngles());
                if (unchanged) continue;
                JSONArray arr = new JSONArray();
                for (double a : angles) arr.put(a);
                ops.add(KitMessages.op("set_preset")
                        .put("name", name).put("angles", arr).toString());
            }
        } catch (JSONException e) {
            return fail("Couldn't build config: " + e.getMessage());
        }
        return true;
    }

    // 7 joint angles from a JSON array, or null if it isn't 7 numbers
    @Nullable
    private static double[] parseAngles(@Nullable JSONArray arr) {
        if (arr == null || arr.length() != PresetItem.JOINT_COUNT) return null;
        double[] out = new double[PresetItem.JOINT_COUNT];
        for (int i = 0; i < out.length; i++) {
            double v = arr.optDouble(i, Double.NaN);
            if (Double.isNaN(v) || Double.isInfinite(v)) return null;
            out[i] = v;
        }
        return out;
    }

    private static String formatLength(double meters) {
        return new java.math.BigDecimal(Double.toString(meters)).stripTrailingZeros().toPlainString();
    }

    private static String orUnknown(String message) {
        return message == null || message.isEmpty() ? "unknown error" : message;
    }

    private boolean fail(String msg) {
        toast(msg);
        return false;
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }
}

package com.example.robotcaregiver;

import android.content.Intent;
import android.os.Bundle;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.textfield.MaterialAutoCompleteTextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;

public class RobotActivity extends AppCompatActivity {

    public static final String EXTRA_CURRENT_HOLSTER = "com.example.robotcaregiver.CURRENT_HOLSTER";
    public static final String EXTRA_CONFIG_OPS = "com.example.robotcaregiver.CONFIG_OPS";
    private static final Pattern TOOL_NAME = Pattern.compile("^[A-Za-z][A-Za-z0-9_]{0,31}$");
    private static final double MAX_TOOL_LENGTH = 1.0;   // meters


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

    private static int nextRequestId = 0;
    private static final List<String> CONFIG_BLOCKS = Arrays.asList("Holster");
    private static final int BLOCK_HOLSTER = 0;

    private DynamicListAdapter adapter;
    private final Map<Integer, Slot> current = new TreeMap<>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_robot);

        // Initialize RecyclerView and its Dynamic Adapter
        RecyclerView recyclerView = findViewById(R.id.recyclerView);
        adapter = new DynamicListAdapter();
        recyclerView.setLayoutManager(new LinearLayoutManager(this));
        recyclerView.setAdapter(adapter);

        // Start from what's already on the kit so edits and deletes can be diffed against it
        loadCurrentHolster(getIntent().getStringExtra(EXTRA_CURRENT_HOLSTER));
        for (Map.Entry<Integer, Slot> e : current.entrySet()) {
            Slot slot = e.getValue();
            adapter.addHolsterItem(String.valueOf(e.getKey()), slot.tool,
                    slot.length == 0 ? "" : formatLength(slot.length));
        }

        // Config block dropdown
        MaterialAutoCompleteTextView dropdownMenu = findViewById(R.id.dropdownMenu);
        dropdownMenu.setAdapter(new ArrayAdapter<>(
                this, android.R.layout.simple_list_item_1, CONFIG_BLOCKS));
        // false = don't filter the list by this text, so every option stays visible
        dropdownMenu.setText(CONFIG_BLOCKS.get(BLOCK_HOLSTER), false);

        // Switch the list's row layout to the chosen block
        dropdownMenu.setOnItemClickListener((parent, view, position, id) -> {
            if (position == BLOCK_HOLSTER) {
                adapter.setListFormat(DynamicListAdapter.VIEW_TYPE_HOLSTER);
            }
        });

        Button btnAddItem = findViewById(R.id.btnAddItem);
        btnAddItem.setOnClickListener(v -> {
            adapter.addNewItem();
            recyclerView.smoothScrollToPosition(Math.max(0, adapter.getItemCount() - 1));
        });

        Button btnDone = findViewById(R.id.btnDone);
        btnDone.setOnClickListener(v -> onDone());
    }

    private void loadCurrentHolster(String json) {
        if (json == null || json.isEmpty()) return;
        try {
            JSONObject tools = new JSONObject(json);
            Iterator<String> names = tools.keys();
            while (names.hasNext()) {
                String name = names.next();
                JSONObject entry = tools.getJSONObject(name);
                current.put(entry.getInt("marker"), new Slot(name, entry.optDouble("length", 0.0)));
            }
        } catch (JSONException e) {
            current.clear();
            toast("Couldn't read the kit's holster: " + e.getMessage());
        }
    }

    private void onDone() {
        // Validate
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
                toast("Row " + (i + 1) + " needs a marker number");
                return;
            }
            if (number <= 0) {
                toast("Row " + (i + 1) + ": marker number must be greater than 0");
                return;
            }
            if (tool.isEmpty()) {
                toast("Marker " + number + " needs a tool name");
                return;
            }
            if (!TOOL_NAME.matcher(tool).matches()) {
                toast("Marker " + number + ": tool name must start with a letter and contain only "
                        + "letters, numbers, or underscores (max 32)");
                return;
            }
            double length = 0.0;
            if (!lengthText.isEmpty()) {
                try {
                    length = Double.parseDouble(lengthText);
                } catch (NumberFormatException e) {
                    length = -1;
                }
                if (!(length >= 0 && length <= MAX_TOOL_LENGTH)) {
                    toast("Marker " + number + ": length must be in meters, from 0 to "
                            + MAX_TOOL_LENGTH);
                    return;
                }
            }
            if (desired.containsKey(number)) {
                toast("Marker " + number + " is used more than once");
                return;
            }
            Integer other = nameToMarker.put(tool.toLowerCase(Locale.ROOT), number);
            if (other != null) {
                toast("'" + tool + "' is in both marker " + other + " and marker " + number);
                return;
            }
            desired.put(number, new Slot(tool, length));
        }

        // Diff against the kit's holster. Clear every removed or changed slot first, then set
        // every changed or new one.
        List<String> ops = new ArrayList<>();
        try {
            for (Map.Entry<Integer, Slot> e : current.entrySet()) {
                if (!e.getValue().sameAs(desired.get(e.getKey()))) {
                    ops.add(op("clear_holster_slot").put("marker_id", e.getKey()).toString());
                }
            }
            for (Map.Entry<Integer, Slot> e : desired.entrySet()) {
                Slot slot = e.getValue();
                if (!slot.sameAs(current.get(e.getKey()))) {
                    ops.add(op("set_holster_slot")
                            .put("marker_id", e.getKey())
                            .put("tool", slot.tool)
                            .put("length", slot.length)
                            .toString());
                }
            }
        } catch (JSONException e) {
            toast("Couldn't build config: " + e.getMessage());
            return;
        }

        if (ops.isEmpty()) {
            // Nothing changed; return without sending anything
            setResult(RESULT_CANCELED);
            finish();
            return;
        }

        setResult(RESULT_OK, new Intent().putExtra(EXTRA_CONFIG_OPS, ops.toArray(new String[0])));
        finish();
    }

    private static String formatLength(double meters) {
        // drop trailing zeros: 0.1 not 0.100000
        return new java.math.BigDecimal(Double.toString(meters)).stripTrailingZeros().toPlainString();
    }

    private static JSONObject op(String name) throws JSONException {
        return new JSONObject().put("op", name).put("id", "tablet-" + (++nextRequestId));
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }
}

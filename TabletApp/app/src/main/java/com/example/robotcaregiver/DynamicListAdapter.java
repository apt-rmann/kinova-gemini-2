package com.example.robotcaregiver;

import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.recyclerview.widget.RecyclerView;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class DynamicListAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {

    public static final int VIEW_TYPE_HOLSTER = 1;
    public static final int VIEW_TYPE_OTHER = 2;
    public static final int VIEW_TYPE_PRESET = 3;

    public interface PresetActions {
        void onMoveArm(PresetItem item);
        void onCapture(PresetItem item);
        void onDelete(PresetItem item);
        // preset that can't be deleted since kit code refers to it by name
        boolean isProtected(PresetItem item);
    }

    private int currentViewType = VIEW_TYPE_HOLSTER;
    private final List<HolsterItem> holsterList = new ArrayList<>();
    private final List<PresetItem> presetList = new ArrayList<>();

    @Nullable private PresetActions presetActions;
    @Nullable private PresetItem freeMoveItem;
    private boolean presetsBusy;

    public void setPresetActions(@Nullable PresetActions actions) {
        this.presetActions = actions;
    }

    // Set the view format depending on config block selection from dropdown
    public void setListFormat(int viewType) {
        if (this.currentViewType == viewType) return;
        this.currentViewType = viewType;
        notifyDataSetChanged();
    }

    public int getListFormat() {
        return currentViewType;
    }

    // Add a new blank item to the active list
    public void addNewItem() {
        if (currentViewType == VIEW_TYPE_HOLSTER) {
            holsterList.add(new HolsterItem(String.valueOf(nextHolsterNumber())));
            notifyItemInserted(holsterList.size() - 1);
        } else if (currentViewType == VIEW_TYPE_PRESET) {
            presetList.add(new PresetItem());
            notifyItemInserted(presetList.size() - 1);
        }
    }

    // Add a pre-filled holster row (e.g. a holster item already in the kit's config)
    public void addHolsterItem(String number, String tool, String length) {
        HolsterItem item = new HolsterItem(number);
        item.setTool(tool);
        item.setLength(length);
        holsterList.add(item);
        if (currentViewType == VIEW_TYPE_HOLSTER) notifyItemInserted(holsterList.size() - 1);
    }

    public List<HolsterItem> getHolsterItems() {
        return Collections.unmodifiableList(holsterList);
    }

    // Add a preset row already in the kit's config
    public void addPresetItem(String name, double[] angles) {
        presetList.add(new PresetItem(name, angles));
        if (currentViewType == VIEW_TYPE_PRESET) notifyItemInserted(presetList.size() - 1);
    }

    public List<PresetItem> getPresetItems() {
        return Collections.unmodifiableList(presetList);
    }

    public void removePresetItem(PresetItem item) {
        int pos = presetList.indexOf(item);
        if (pos < 0) return;
        presetList.remove(pos);
        if (currentViewType == VIEW_TYPE_PRESET) notifyItemRemoved(pos);
    }

    //Redraw preset row
    public void refreshPresetItem(PresetItem item) {
        int pos = presetList.indexOf(item);
        if (pos >= 0 && currentViewType == VIEW_TYPE_PRESET) notifyItemChanged(pos);
    }

    //freeMoveItem - row that was used to set arm in admittance mode,  busy - is phone waiting for kit reply
    public void setPresetLock(@Nullable PresetItem freeMoveItem, boolean busy) {
        this.freeMoveItem = freeMoveItem;
        this.presetsBusy = busy;
        if (currentViewType == VIEW_TYPE_PRESET) notifyDataSetChanged();
    }

    // Highest valid number currently in the list + 1
    private int nextHolsterNumber() {
        int max = 0;
        for (HolsterItem item : holsterList) {
            try {
                max = Math.max(max, Integer.parseInt(item.getNumber().trim()));
            } catch (NumberFormatException ignored) {
                // Blank/invalid numbers are skipped
            }
        }
        return max + 1;
    }

    @Override
    public int getItemViewType(int position) {
        return currentViewType;
    }

    @NonNull
    @Override
    public RecyclerView.ViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        LayoutInflater inflater = LayoutInflater.from(parent.getContext());
        if (viewType == VIEW_TYPE_PRESET) {
            View view = inflater.inflate(R.layout.preset, parent, false);
            return new PresetViewHolder(view);
        } else if (viewType == VIEW_TYPE_HOLSTER) {
            View view = inflater.inflate(R.layout.holster, parent, false);
            return new HolsterViewHolder(view);
        } else {
            // TODO future menu items
            View view = inflater.inflate(R.layout.holster, parent, false);
            return new HolsterViewHolder(view);
        }
    }

    @Override
    public void onBindViewHolder(@NonNull RecyclerView.ViewHolder holder, int position) {
        if (holder instanceof PresetViewHolder) {
            bindPreset((PresetViewHolder) holder, presetList.get(position));
        } else if (holder instanceof HolsterViewHolder) {
            HolsterViewHolder holsterHolder = (HolsterViewHolder) holder;
            HolsterItem item = holsterList.get(position);

            // Detach old watchers so setText doesn't write into the previous item
            holsterHolder.removeTextWatchers();
            holsterHolder.number.setText(item.getNumber());
            holsterHolder.tool.setText(item.getTool());
            holsterHolder.length.setText(item.getLength());

            holsterHolder.setTextWatchers(
                    new SimpleWatcher(item::setNumber),
                    new SimpleWatcher(item::setTool),
                    new SimpleWatcher(item::setLength));

            holsterHolder.delete.setOnClickListener(v -> {
                int currentPos = holsterHolder.getAdapterPosition();
                if (currentPos != RecyclerView.NO_POSITION) {
                    holsterList.remove(currentPos);
                    notifyItemRemoved(currentPos);
                }
            });
        }
    }

    private void bindPreset(PresetViewHolder h, PresetItem item) {
        boolean isProtected = presetActions != null && presetActions.isProtected(item);
        boolean locked = presetsBusy || freeMoveItem != null;   // free-move or waiting -> lock rows
        boolean isFreeRow = item == freeMoveItem;

        h.removeTextWatcher();
        h.name.setText(item.getName());
        h.setTextWatcher(new SimpleWatcher(item::setName));
        h.angles.setText(item.formatAngles());

        h.name.setEnabled(!locked && !isProtected);
        h.delete.setVisibility(isProtected ? View.INVISIBLE : View.VISIBLE);
        h.delete.setEnabled(!locked && !isProtected);
        h.moveArm.setEnabled(!locked);
        h.moveArm.setText(isFreeRow ? "Arm is free" : "Move arm");
        // Capture stays available on the free row, use to record the angles then lock the arm again
        h.capture.setEnabled(!presetsBusy && (freeMoveItem == null || isFreeRow));

        h.moveArm.setOnClickListener(v -> {
            if (presetActions != null) presetActions.onMoveArm(item);
        });
        h.capture.setOnClickListener(v -> {
            if (presetActions != null) presetActions.onCapture(item);
        });
        h.delete.setOnClickListener(v -> {
            if (presetActions != null) presetActions.onDelete(item);
        });
    }

    @Override
    public int getItemCount() {
        if (currentViewType == VIEW_TYPE_HOLSTER) return holsterList.size();
        if (currentViewType == VIEW_TYPE_PRESET) return presetList.size();
        return 0;
    }

    private interface TextSink {
        void accept(String text);
    }

    private static class SimpleWatcher implements TextWatcher {
        private final TextSink sink;

        SimpleWatcher(TextSink sink) {
            this.sink = sink;
        }

        @Override
        public void beforeTextChanged(CharSequence s, int start, int count, int after) {}

        @Override
        public void onTextChanged(CharSequence s, int start, int before, int count) {
            sink.accept(s.toString());
        }

        @Override
        public void afterTextChanged(Editable s) {}
    }

    // ViewHolder specific to your 'Holster' row elements
    public static class HolsterViewHolder extends RecyclerView.ViewHolder {
        final EditText number;
        final EditText tool;
        final EditText length;
        final ImageButton delete;
        private TextWatcher numberWatcher;
        private TextWatcher toolWatcher;
        private TextWatcher lengthWatcher;

        public HolsterViewHolder(@NonNull View itemView) {
            super(itemView);
            number = itemView.findViewById(R.id.markerId);
            tool = itemView.findViewById(R.id.tool);
            length = itemView.findViewById(R.id.length);
            delete = itemView.findViewById(R.id.delete);
        }

        void setTextWatchers(TextWatcher numberWatcher, TextWatcher toolWatcher,
                             TextWatcher lengthWatcher) {
            this.numberWatcher = numberWatcher;
            this.toolWatcher = toolWatcher;
            this.lengthWatcher = lengthWatcher;
            number.addTextChangedListener(numberWatcher);
            tool.addTextChangedListener(toolWatcher);
            length.addTextChangedListener(lengthWatcher);
        }

        void removeTextWatchers() {
            if (numberWatcher != null) number.removeTextChangedListener(numberWatcher);
            if (toolWatcher != null) tool.removeTextChangedListener(toolWatcher);
            if (lengthWatcher != null) length.removeTextChangedListener(lengthWatcher);
            numberWatcher = null;
            toolWatcher = null;
            lengthWatcher = null;
        }
    }

    // ViewHolder for a 'Presets' row
    public static class PresetViewHolder extends RecyclerView.ViewHolder {
        final EditText name;
        final TextView angles;
        final Button moveArm;
        final Button capture;
        final ImageButton delete;
        private TextWatcher nameWatcher;

        public PresetViewHolder(@NonNull View itemView) {
            super(itemView);
            name = itemView.findViewById(R.id.presetName);
            angles = itemView.findViewById(R.id.presetAngles);
            moveArm = itemView.findViewById(R.id.presetMoveArm);
            capture = itemView.findViewById(R.id.presetCapture);
            delete = itemView.findViewById(R.id.presetDelete);
        }

        void setTextWatcher(TextWatcher watcher) {
            nameWatcher = watcher;
            name.addTextChangedListener(watcher);
        }

        void removeTextWatcher() {
            if (nameWatcher != null) name.removeTextChangedListener(nameWatcher);
            nameWatcher = null;
        }
    }
}

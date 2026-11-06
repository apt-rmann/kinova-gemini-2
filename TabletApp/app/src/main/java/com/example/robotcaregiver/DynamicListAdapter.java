package com.example.robotcaregiver;

import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.ImageButton;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class DynamicListAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {

    public static final int VIEW_TYPE_HOLSTER = 1;
    public static final int VIEW_TYPE_OTHER = 2;

    private int currentViewType = VIEW_TYPE_HOLSTER;
    // Kept across dropdown switches so entered data isn't lost
    private final List<HolsterItem> holsterList = new ArrayList<>();

    // Set the view format depending on dropdown selection
    public void setListFormat(int viewType) {
        if (this.currentViewType == viewType) return;
        this.currentViewType = viewType;
        notifyDataSetChanged();
    }

    // Add a new blank item to the active list style
    public void addNewItem() {
        if (currentViewType == VIEW_TYPE_HOLSTER) {
            holsterList.add(new HolsterItem(String.valueOf(nextHolsterNumber())));
            notifyItemInserted(holsterList.size() - 1);
        }
    }

    // Add a pre-filled holster row (e.g. a slot already in the kit's config)
    public void addHolsterItem(String number, String tool, String length) {
        HolsterItem item = new HolsterItem(number);
        item.setTool(tool);
        item.setLength(length);
        holsterList.add(item);
        notifyItemInserted(holsterList.size() - 1);
    }

    public List<HolsterItem> getHolsterItems() {
        return Collections.unmodifiableList(holsterList);
    }

    // Highest valid number currently in the list + 1 (starts at 1)
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
        if (viewType == VIEW_TYPE_HOLSTER) {
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
        if (holder instanceof HolsterViewHolder) {
            HolsterViewHolder holsterHolder = (HolsterViewHolder) holder;
            HolsterItem item = holsterList.get(position);

            // Detach old watchers so setText doesn't write into the previously bound item
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

    @Override
    public int getItemCount() {
        return currentViewType == VIEW_TYPE_HOLSTER ? holsterList.size() : 0;
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
}

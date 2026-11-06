package com.example.robotcaregiver;

import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.ArrayList;
import java.util.List;

public class DynamicListAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {

    public static final int VIEW_TYPE_HOLSTER = 1;
    public static final int VIEW_TYPE_OTHER = 2;

    private int currentViewType = VIEW_TYPE_HOLSTER;
    private final List<HolsterItem> holsterList = new ArrayList<>();

    // Set the view format depending on dropdown selection
    public void setListFormat(int viewType) {
        this.currentViewType = viewType;
        this.holsterList.clear(); // Clear old list items when switching formats
        notifyDataSetChanged();
    }

    // Add a new blank item to the active list style
    public void addNewItem() {
        if (currentViewType == VIEW_TYPE_HOLSTER) {
            holsterList.add(new HolsterItem());
            notifyItemInserted(holsterList.size() - 1);
        }
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

            // Set up auto-incremented numbering (1-based index)
            holsterHolder.markerId.setText((position + 1) + ".");

            // Temporarily remove TextWatcher to avoid infinite loops while binding data
            holsterHolder.removeTextWatcher();
            holsterHolder.tool.setText(item.getTool());

            // Re-attach TextWatcher to save typing states directly back to our model list
            holsterHolder.addTextWatcher(new TextWatcher() {
                @Override
                public void beforeTextChanged(CharSequence s, int start, int count, int after) {}

                @Override
                public void onTextChanged(CharSequence s, int start, int before, int count) {
                    item.setTool(s.toString());
                }

                @Override
                public void afterTextChanged(Editable s) {}
            });

            // Renumbering deletion logic
            holsterHolder.delete.setOnClickListener(v -> {
                int currentPos = holsterHolder.getAdapterPosition();
                if (currentPos != RecyclerView.NO_POSITION) {
                    holsterList.remove(currentPos);
                    notifyItemRemoved(currentPos);
                    // Shifts items up and updates numbering labels on subsequent rows
                    notifyItemRangeChanged(currentPos, holsterList.size() - currentPos);
                }
            });
        }
    }

    @Override
    public int getItemCount() {
        return currentViewType == VIEW_TYPE_HOLSTER ? holsterList.size() : 0;
    }

    // ViewHolder specific to your 'Holster' row elements
    public static class HolsterViewHolder extends RecyclerView.ViewHolder {
        final TextView markerId;
        final EditText tool;
        final ImageButton delete;
        private TextWatcher activeTextWatcher;

        public HolsterViewHolder(@NonNull View itemView) {
            super(itemView);
            markerId = itemView.findViewById(R.id.markerId);
            tool = itemView.findViewById(R.id.tool);
            delete = itemView.findViewById(R.id.delete);
        }

        public void addTextWatcher(TextWatcher watcher) {
            this.activeTextWatcher = watcher;
            tool.addTextChangedListener(activeTextWatcher);
        }

        public void removeTextWatcher() {
            if (activeTextWatcher != null) {
                tool.removeTextChangedListener(activeTextWatcher);
            }
        }
    }
}
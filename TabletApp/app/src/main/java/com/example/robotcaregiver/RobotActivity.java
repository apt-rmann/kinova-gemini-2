package com.example.robotcaregiver;

import android.os.Bundle;
import android.widget.ArrayAdapter;
import android.widget.AutoCompleteTextView;
import android.widget.Button;

import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import java.util.Arrays;
import java.util.List;

public class RobotActivity extends AppCompatActivity {


    private DynamicListAdapter adapter;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_robot);

        // Initialize RecyclerView and its Dynamic Adapter
        RecyclerView recyclerView = findViewById(R.id.recyclerView);
        adapter = new DynamicListAdapter();
        recyclerView.setLayoutManager(new LinearLayoutManager(this));
        recyclerView.setAdapter(adapter);

        // Setup Predetermined Dropdown Menu Selection Items
        List<String> dropDownItems = Arrays.asList("Holster", "Magazines", "Accessories");
        AutoCompleteTextView dropdownMenu = findViewById(R.id.dropdownMenu);
        ArrayAdapter<String> arrayAdapter = new ArrayAdapter<>(this, android.R.layout.simple_dropdown_item_1line, dropDownItems);

        dropdownMenu.setAdapter(arrayAdapter);
        // Default text selection layout on start
        dropdownMenu.setText(dropDownItems.get(0), false);

        // Dynamically toggle adapter layouts depending on item selections
        dropdownMenu.setOnItemClickListener((parent, view, position, id) -> {
            switch (position) {
                case 0:
                    adapter.setListFormat(DynamicListAdapter.VIEW_TYPE_HOLSTER);
                    break;
                case 1:
                case 2:
                    adapter.setListFormat(DynamicListAdapter.VIEW_TYPE_OTHER); // Placeholders
                    break;
            }
        });

        // Add Button interaction rules
        Button btnAddItem = findViewById(R.id.btnAddItem);
        btnAddItem.setOnClickListener(v -> {
            adapter.addNewItem();
            // Automatically scroll list view down to the newest item view layout
            recyclerView.smoothScrollToPosition(adapter.getItemCount());
        });
    }
}

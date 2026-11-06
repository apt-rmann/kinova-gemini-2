package com.example.robotcaregiver;

public class HolsterItem {
    // Kept as Strings so the fields can be temporarily empty while the user edits them
    private String number;
    private String tool;
    private String length;   // meters; blank = 0

    public HolsterItem() {
        this("");
    }

    public HolsterItem(String number) {
        this.number = number;
        this.tool = "";
        this.length = "";
    }

    public String getNumber() {
        return number;
    }

    public void setNumber(String number) {
        this.number = number;
    }

    public String getTool() {
        return tool;
    }

    public void setTool(String tool) {
        this.tool = tool;
    }

    public String getLength() {
        return length;
    }

    public void setLength(String length) {
        this.length = length;
    }
}

package com.example.robotcaregiver;

import androidx.annotation.Nullable;

import java.util.Locale;

/** One row of the presets editor (config.yaml `presets:`: name -> 7 joint angles, degrees). */
public class PresetItem {
    public static final int JOINT_COUNT = 7;

    @Nullable private final String originalName;
    @Nullable private final double[] originalAngles;
    private String name;
    @Nullable private double[] angles;

    public PresetItem() {
        this(null, null);
    }

    public PresetItem(@Nullable String originalName, @Nullable double[] originalAngles) {
        this.originalName = originalName;
        this.originalAngles = originalAngles == null ? null : originalAngles.clone();
        this.name = originalName == null ? "" : originalName;
        this.angles = this.originalAngles == null ? null : this.originalAngles.clone();
    }

    @Nullable
    public String getOriginalName() {
        return originalName;
    }

    @Nullable
    public double[] getOriginalAngles() {
        return originalAngles == null ? null : originalAngles.clone();
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    @Nullable
    public double[] getAngles() {
        return angles == null ? null : angles.clone();
    }

    public void setAngles(@Nullable double[] angles) {
        this.angles = angles == null ? null : angles.clone();
    }

    public String formatAngles() {
        if (angles == null) return "Angles: not captured";
        StringBuilder sb = new StringBuilder("Angles: ");
        for (int i = 0; i < angles.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(String.format(Locale.ROOT, "%.1f", angles[i]));
        }
        return sb.toString();
    }
}

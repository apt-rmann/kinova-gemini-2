package com.example.robotcaregiver;

import static android.view.View.GONE;
import static android.view.View.VISIBLE;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.CountDownTimer;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.util.Base64;
import android.util.Log;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ImageButton;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.media3.common.MediaItem;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

import org.json.JSONException;
import org.json.JSONObject;
import org.vosk.LibVosk;
import org.vosk.LogLevel;
import org.vosk.Model;
import org.vosk.Recognizer;
import org.vosk.android.RecognitionListener;
import org.vosk.android.SpeechService;
import org.vosk.android.StorageService;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Locale;

public class MainActivity extends AppCompatActivity
        implements TcpClientKitLink.Listener, RecognitionListener {

    private static final boolean DEBUGGING = false;

    private static final String TAG = "RobotCaregiverVoiceControl";
    private static final String MODEL_ASSET_DIR = "model-en-us";
    private static final float SAMPLE_RATE = 16000.0f;

    private static final String[] WAKE_VARIANTS = {
            "hey robot", "hey robert", "hey rob it", "a robot", "hey robo"
    };

    private static final long COMMAND_TIMEOUT_MS = 8000;
    private enum Mode { LOADING, WAKE, COMMAND, OFF }
    private final Handler ui = new Handler(Looper.getMainLooper());
    private Model model;
    private SpeechService speechService;
    private Mode mode = Mode.LOADING;
    private Runnable commandTimeoutTask;
    private boolean awaitingWakeCallEnd = false;


    private TextView statusView;
    private Button startStopButton;
    private WebView frameView;
    private ImageButton settingsButton;
    private Button connectButton;

    private KitLink kit;
    private AppSettings settings;
    private ActivityResultLauncher<Intent> settingsLauncher;
    private ActivityResultLauncher<String[]> permissionLauncher;

    private String connectedHost;
    private static final int KIT_PORT = 9100;
    private static final long CONNECT_TIMEOUT = 30000;
    private CountDownTimer connectTimer;



    @Override
    protected void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusView      = findViewById(R.id.statusView);
        startStopButton = findViewById(R.id.startStopButton);
        settingsButton  = findViewById(R.id.settingsButton);
        connectButton   = findViewById(R.id.connectButton);
        frameView       = findViewById(R.id.frameView);
        WebSettings webSettings = frameView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setLoadWithOverviewMode(true);
        webSettings.setUseWideViewPort(true);
        webSettings.setAllowFileAccess(true);
        webSettings.setAllowContentAccess(true);
        webSettings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        frameView.setWebViewClient(new WebViewClient());

        statusView.setMovementMethod(new ScrollingMovementMethod());
        settings = new AppSettings(this);

        LibVosk.setLogLevel(LogLevel.WARNINGS);

        settingsLauncher = registerForActivityResult(
                new ActivityResultContracts.StartActivityForResult(),
                result -> onSettingsClosed());

        permissionLauncher = registerForActivityResult(
                new ActivityResultContracts.RequestMultiplePermissions(),
                granted -> {
                    if (hasMicPermission()) {
                        loadModel();
                    } else {
                        addStatus("Microphone permission denied — voice control is off.");
                        mode = Mode.OFF;
                        updateVoiceUi();
                    }
                });

        startStopButton.setOnClickListener(v -> {
            if (mode == Mode.COMMAND) {
                endCommandMode();
            } else if (mode == Mode.WAKE) {
                beginCommandMode(false);   // manual trigger, skips the wake phrase
            }
        });

        settingsButton.setOnClickListener(v ->
                settingsLauncher.launch(new Intent(this, SettingsActivity.class)));
        connectButton.setOnClickListener(v -> connectToKit());

        setConnectedUi(false);
        addStatus("Not connected to kit! Say \u201CHey Robot, Connect\u201D.");

        updateVoiceUi();
        requestRuntimePermissions();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        stopRecognition();
        if (model != null) {
            model.close();
            model = null;
        }
        cancelConnectCountdown();
        if (kit != null) kit.disconnect();
    }

    private void loadModel() {
        mode = Mode.LOADING;
        updateVoiceUi();
        if(DEBUGGING) addStatus("Loading speech model\u2026");

        StorageService.unpack(this, MODEL_ASSET_DIR, "model",
            loadedModel -> {
                model = loadedModel;
                if(DEBUGGING) addStatus("Speech model ready.");
                startRecognition();
            },
            exception -> {
                addStatus("Could not load speech model: " + exception.getMessage());
                addStatus("Check that assets/" + MODEL_ASSET_DIR
                        + " holds the unzipped model (am/, conf/, graph/\u2026).");
                mode = Mode.OFF;
                updateVoiceUi();
            });
    }

    private void startRecognition() {
        if (speechService != null) return;
        if (model == null || !hasMicPermission()) return;

        try {
            Recognizer recognizer = new Recognizer(model, SAMPLE_RATE);
            speechService = new SpeechService(recognizer, SAMPLE_RATE);
            speechService.startListening(this);
            mode = Mode.WAKE;
            Log.d(TAG, "vosk listening");
        } catch (IOException e) {
            addStatus("Could not start the recognizer: " + e.getMessage());
            mode = Mode.OFF;
        }
        updateVoiceUi();
    }

    private void stopRecognition() {
        cancelCommandTimeout();
        ui.removeCallbacksAndMessages(null);
        if (speechService != null) {
            speechService.stop();
            speechService.shutdown();
            speechService = null;
        }
        mode = Mode.OFF;
    }

    @Override
    public void onPartialResult(String hypothesis) {

        if (mode == Mode.WAKE) {
            String text = extractText(hypothesis, "partial");
            if (text.isEmpty()) return;

            if (wakePhraseTail(text) != null) {
                beginCommandMode(true);
            }
            return;
        }

        if(mode == Mode.COMMAND && !awaitingWakeCallEnd){
            armCommandTimeout();
        }

    }

    @Override
    public void onResult(String hypothesis) {
        String text = extractText(hypothesis, "text");
        if (text.isEmpty()) return;

        Log.d(TAG, "utterance (" + mode + "): " + text);

        if(awaitingWakeCallEnd){
            awaitingWakeCallEnd = false;
            String tail = wakePhraseTail(text);
            if(tail != null && !tail.isEmpty()){
                handleCommand(tail);
                mode = mode = Mode.WAKE;
                updateVoiceUi();
            }

            else{
                armCommandTimeout();
            }

            return;
        }

        if (mode == Mode.COMMAND) {
            cancelCommandTimeout();

            String tail = wakePhraseTail(text);
            handleCommand(tail != null && !tail.isEmpty() ? tail : text);
            mode = Mode.WAKE;
            updateVoiceUi();
            return;
        }

        if (mode == Mode.WAKE) {
            String tail = wakePhraseTail(text);
            if (tail == null) return;
            if (!tail.isEmpty()) {
                // "hey robot, move forward" spoken in one breath
                handleCommand(tail);
            } else {
                beginCommandMode(false);
            }
        }
    }

    @Override
    public void onFinalResult(String hypothesis) {}

    @Override
    public void onError(Exception exception) {
        addStatus("Recognizer error: " + exception.getMessage());
        mode = Mode.OFF;
        updateVoiceUi();
    }

    @Override
    public void onTimeout() {}


    private String extractText(@Nullable String json, @NonNull String key) {
        if (json == null) return "";
        try {
            return new JSONObject(json)
                    .optString(key, "")
                    .toLowerCase(Locale.US)
                    .trim();
        } catch (JSONException e) {
            Log.w(TAG, "unparseable hypothesis: " + json);
            return "";
        }
    }

    private void beginCommandMode(boolean fromPartial) {
        if (mode == Mode.COMMAND) return;
        mode = Mode.COMMAND;
        awaitingWakeCallEnd = fromPartial;
        updateVoiceUi();
        setStatus("Listening...");

         if(!fromPartial) armCommandTimeout();
    }

    private void endCommandMode() {
        cancelCommandTimeout();
        awaitingWakeCallEnd = false;
        if (mode != Mode.COMMAND) return;
        mode = Mode.WAKE;
        updateVoiceUi();
    }

    private void armCommandTimeout() {

        if(commandTimeoutTask != null) return;

        commandTimeoutTask = () -> {
            commandTimeoutTask = null;
            endCommandMode();
            cancelCommandTimeout();
        };

        ui.postDelayed(commandTimeoutTask, COMMAND_TIMEOUT_MS);
    }

    private void cancelCommandTimeout() {
        if (commandTimeoutTask != null) {
            ui.removeCallbacks(commandTimeoutTask);
            commandTimeoutTask = null;
        }
    }

    @Nullable
    private String wakePhraseTail(@NonNull String text) {
        String s = normalize(text);
        for (String variant : WAKE_VARIANTS) {
            int idx = s.indexOf(variant);
            if (idx >= 0) {
                return s.substring(idx + variant.length()).trim();
            }
        }
        return null;
    }

    private static String normalize(@NonNull String s) {
        return s.toLowerCase(Locale.US)
                .replaceAll("[^a-z0-9 ]", "")
                .replaceAll("\\s+", " ")
                .trim();
    }

    private void handleCommand(@NonNull String raw) {
        String text = normalize(raw);
        if (text.isEmpty()) return;

        setStatus("You: " + text);

        if (text.contains("never mind") || text.contains("cancel")) {
            return;
        }
        if (text.contains("connect")) {
            connectToKit();
            return;
        }
        if (kit == null || !kit.isConnected()) {
            addStatus("Not connected to the kit — say \u201Chey robot, connect\u201D first.");
            return;
        }
        kit.sendInstruction(text);
    }

    private void updateVoiceUi() {
        runOnUiThread(() -> {
            switch (mode) {
                case COMMAND:
                    startStopButton.setText("Listening\u2026");
                    startStopButton.setBackgroundTintList(
                            ColorStateList.valueOf(Color.parseColor("#2E7D32")));
                    startStopButton.setEnabled(true);
                    break;
                case WAKE:
                    startStopButton.setText("Say \u201CHey Robot\u201D");
                    startStopButton.setBackgroundTintList(
                            ColorStateList.valueOf(Color.parseColor("#37474F")));
                    startStopButton.setEnabled(true);
                    break;
                case LOADING:
                    startStopButton.setText("Loading\u2026");
                    startStopButton.setBackgroundTintList(
                            ColorStateList.valueOf(Color.parseColor("#9E9E9E")));
                    startStopButton.setEnabled(false);
                    break;
                default:
                    startStopButton.setText("Voice off");
                    startStopButton.setBackgroundTintList(
                            ColorStateList.valueOf(Color.parseColor("#9E9E9E")));
                    startStopButton.setEnabled(false);
            }
        });
    }

    private void onSettingsClosed() {
        String newHost = settings.getKitHost();
        if (newHost == null || newHost.isEmpty()) {
            addStatus("No kit address set. Open Settings and enter the kit's IP.");
            setConnectedUi(false);
            return;
        }
        if (!newHost.equals(connectedHost) || kit == null || !kit.isConnected()) {
            if(DEBUGGING) addStatus("Address changed. Reconnecting to " + newHost + "\u2026");
            connectToKit();
        }
    }

    private void connectToKit() {
        String host = settings.getKitHost();
        String type = settings.isBluetooth() ? "Bluetooth"
                : (settings.isBle() ? "BLE" : "WiFi");

        if (type.equals("WiFi") && (host == null || host.isEmpty())) {
            addStatus("No kit address set. Open Settings and enter the kit's IP.");
            setConnectedUi(false);
            return;
        }

        if (kit != null) {
            kit.setListener(null);
            kit.disconnect();
        }

        connectedHost = host;
        kit = settings.isBluetooth()
                ? new BluetoothKitLink(settings.getKitMac(), settings.getChannel(), this)
                : (settings.isBle() ? new BleKitLink(this) : new TcpClientKitLink(host, KIT_PORT, this));
        kit.setListener(this);

        addStatus("Connecting over " + type + "\u2026");
        startConnectCountdown();
        kit.connect();
    }

    private void startConnectCountdown() {
        runOnUiThread(() -> {
            cancelConnectCountdown();
            connectButton.setVisibility(VISIBLE);
            connectButton.setEnabled(false);

            connectTimer = new CountDownTimer(CONNECT_TIMEOUT, 1000) {
                @Override public void onTick(long l) {
                    connectButton.setText("Connecting\u2026 " + ((l + 999) / 1000) + "s");
                }
                @Override public void onFinish() {
                    connectTimer = null;
                    addStatus("Could not connect within 30 seconds. Tap Connect to try again.");
                    connectButton.setText("Connect");
                    connectButton.setEnabled(true);
                    if (kit != null) {
                        kit.setListener(null);
                        kit.disconnect();
                        kit.setListener(MainActivity.this);
                    }
                }
            }.start();
        });
    }

    private void cancelConnectCountdown() {
        if (connectTimer != null) {
            connectTimer.cancel();
            connectTimer = null;
        }
    }

    private void setConnectedUi(boolean connected) {
        runOnUiThread(() -> {
            if (connected) {
                addStatus("Connected to kit!");
                cancelConnectCountdown();
                connectButton.setVisibility(GONE);
            } else {
                connectButton.setText("Connect");
                connectButton.setVisibility(VISIBLE);
                connectButton.setEnabled(true);
            }
        });
    }

    @Override public void onKitStatus(@NonNull String status) {
        runOnUiThread(() -> setStatus("Kit: " + status));
    }

    @Override public void onKitConnectionChanged(boolean connected, @NonNull String detail) {
        setConnectedUi(connected);
        runOnUiThread(() -> toast(detail));
    }

    @Override public void onDbgMsg(@NonNull String msg) {
        if(DEBUGGING) runOnUiThread(() -> addStatus("Dbg: " + msg));
    }

    @Override
    public void onKitCamera(String streamUrl) {
        runOnUiThread(() -> {
            // Mimic the original layout using an HTML payload injected locally
            String htmlData = "<html><head><style>"
                    + "body { margin: 0; background-color: #1a1a1a; display: flex; justify-content: center; align-items: center; height: 100vh; }"
                    + "img { width: 100%; height: 100%; object-fit: contain; }"
                    + "</style></head><body>"
                    + "<img src=\"" + streamUrl + "\" alt=\"Camera Feed Link\">"
                    + "</body></html>";

            // Load the loopback network content string into the UI container window
            frameView.loadDataWithBaseURL(null, htmlData, "text/html", "UTF-8", null);
        });
    }

    @Override
    public void onKitCamera(byte[] image){

        String base64Image = Base64.encodeToString(image, Base64.DEFAULT);

        String htmlData = "<html><head><style>"
                + "body { margin: 0; background-color: #1a1a1a; display: flex; justify-content: center; align-items: center; height: 100vh; }"
                + "img { width: 100%; height: 100%; object-fit: contain; }"
                + "</style></head><body>"
                + "<img src='data:image/png;base64,"
                + base64Image
                + "' />"
                + "</body></html>";

        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                frameView.loadDataWithBaseURL("file:///android_asset/", htmlData, "text/html", "utf-8", "");
            }
        });
    }

    private void addStatus(@NonNull String text) {
        runOnUiThread(() -> {
            statusView.setText(statusView.getText().toString() + "\n" + text);
            String s = statusView.getText().toString();
            if (s.length() > 8000) statusView.setText(s.substring(s.length() - 6000));
        });
    }

    private void setStatus(@NonNull String text) {
        runOnUiThread(() -> { statusView.setText(text); });
    }

    private void toast(@NonNull String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }

    private boolean hasMicPermission() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void requestRuntimePermissions() {
        ArrayList<String> needed = new ArrayList<>();
        if (!hasMicPermission()) needed.add(Manifest.permission.RECORD_AUDIO);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            String[] bt = {
                    Manifest.permission.BLUETOOTH_CONNECT,
                    Manifest.permission.BLUETOOTH_SCAN,
                    Manifest.permission.BLUETOOTH_ADVERTISE
            };
            for (String p : bt) {
                if (ContextCompat.checkSelfPermission(this, p)
                        != PackageManager.PERMISSION_GRANTED) {
                    needed.add(p);
                }
            }
        }

        //permissions all already granted, still load the vosk model
        if (needed.isEmpty()) {
            loadModel();
        } else {
            permissionLauncher.launch(needed.toArray(new String[0]));
        }
    }
}

package com.example.robotcaregiver;

import static android.view.View.GONE;
import static android.view.View.VISIBLE;

import android.Manifest;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
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
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.media3.common.MediaItem;
import androidx.media3.common.util.NonNullApi;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
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
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public class MainActivity extends AppCompatActivity
        implements TcpClientKitLink.Listener, RecognitionListener {

    private static final boolean DEBUGGING = false;
    private static final String PREFS = "robot_caregiver_settings";
    public static final String SPEAK_MODE = "speakMode";

    private static final String TAG = "RobotCaregiverVoiceControl";
    private static final String MODEL_ASSET_DIR = "model-en-us";
    private static final float SAMPLE_RATE = 16000.0f;

    private static final String[] WAKE_VARIANTS = {
            "hey robot", "hey robert", "hey rob it", "a robot", "hey robo"
    };

    private static final String STOP_WORD = "I'm finished";
    private static final float MIN_WORD_CONF = 0.8f;
    private static final Set<String> FILLER = new HashSet<>(Arrays.asList("the", "a", "uh", "um", "huh", "hm", "oh", "ah", "mm"));
    private static StringBuilder commandBuffer = new StringBuilder();
    private String lastPartial = "";

    private static final long COMMAND_TIMEOUT_MS = 8000;
    private static final long SILENCE_TIMEOUT_MS = 2000;
    private enum Mode { LOADING, WAKE, COMMAND, OFF }
    private static final int VIEW_TYPE_YOU = 1;
    private static final int VIEW_TYPE_ROBOT = 2;
    private static final int VIEW_TYPE_PHONE = 3;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private Model model;
    private SpeechService speechService;
    private Mode mode = Mode.LOADING;
    private boolean speakMode = false;
    private SharedPreferences prefs;
    private Runnable commandTimeoutTask;
    private boolean awaitingWakeCallEnd = false;


    private RecyclerView recyclerView;
    private ChatAdapter chatAdapter;
    private List<ChatMessage> messageList;
    private Button startStopButton;
    private ImageButton speakButton;
    private LinearLayout layoutChatInput;
    private EditText messageBox;
    private ImageButton sendMessage;
    private WebView frameView;
    private ImageButton settingsButton;
    private ImageButton robotButton;
    private Button connectButton;

    private KitLink kit;
    private AppSettings settings;
    private ActivityResultLauncher<Intent> settingsLauncher;
    private ActivityResultLauncher<Intent> robotLauncher;
    private ActivityResultLauncher<String[]> permissionLauncher;

    // "<transport>:<address>" of the current/last connection, so Settings changes can reconnect
    private String connectedTarget;
    // Lines starting with this go to the kit's /config_update topic (config_editor), not the model
    private static final String CONFIG_PREFIX = "CONFIG ";
    // Kit's holster (config.yaml `tools:` block, {"<tool>": {"marker": id, "length": m}}) from
    // its last config reply; null = not known
    @Nullable private JSONObject kitHolster;
    private static final int KIT_PORT = 9100;
    private static final long CONNECT_TIMEOUT = 30000;
    private CountDownTimer connectTimer;



    @Override
    protected void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        messageList     = new ArrayList<>();
        recyclerView    = findViewById(R.id.recycler_chat);
        startStopButton = findViewById(R.id.startStopButton);
        speakButton     = findViewById(R.id.speakMode);
        layoutChatInput = findViewById(R.id.layout_chat_input);
        messageBox      = findViewById(R.id.et_message_box);
        sendMessage     = findViewById(R.id.btn_send_message);
        settingsButton  = findViewById(R.id.settingsButton);
        robotButton     = findViewById(R.id.robotButton);
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

        LinearLayoutManager layoutManager = new LinearLayoutManager(this);
        layoutManager.setStackFromEnd(true);
        recyclerView.setLayoutManager(layoutManager);
        chatAdapter = new ChatAdapter(messageList);
        recyclerView.setAdapter(chatAdapter);

        settings = new AppSettings(this);

        LibVosk.setLogLevel(LogLevel.WARNINGS);

        findSpeakMode();

        settingsLauncher = registerForActivityResult(
                new ActivityResultContracts.StartActivityForResult(),
                result -> onSettingsClosed());

        robotLauncher = registerForActivityResult(
                new ActivityResultContracts.StartActivityForResult(),
                result -> onRobotClosed(result.getResultCode(), result.getData()));

        permissionLauncher = registerForActivityResult(
                new ActivityResultContracts.RequestMultiplePermissions(),
                granted -> {
                    if(speakMode){
                        if (hasMicPermission()) {
                            loadModel();
                        } else {
                            addStatus("Microphone permission denied — voice control is off.", VIEW_TYPE_PHONE);
                            mode = Mode.OFF;
                            updateVoiceUi();
                        }
                    }

                });

        startStopButton.setOnClickListener(v -> {
            if(speakMode){
                if (mode == Mode.COMMAND) {
                    endCommandMode();
                } else if (mode == Mode.WAKE) {
                    beginCommandMode(false);   // manual trigger, skips the wake phrase
                }
            }

            else{
                //pull up keyboard w/ message box and send button
                layoutChatInput.setVisibility(View.VISIBLE);

                messageBox.requestFocus();
                InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
                if (imm != null) {
                    imm.showSoftInput(messageBox, InputMethodManager.SHOW_IMPLICIT);
                }
            }

        });

        sendMessage.setOnClickListener(v -> {submitMessage();});
        messageBox.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                submitMessage();
                return true;
            }
            return false;
        });

        speakButton.setOnClickListener(v -> {
            speakMode = !speakMode;
            prefs.edit().putBoolean(SPEAK_MODE, speakMode).apply();
            updateVoiceUi();
            if(!speakMode) stopRecognition();
            else startRecognition();
        });

        settingsButton.setOnClickListener(v ->
                settingsLauncher.launch(new Intent(this, SettingsActivity.class)));
        connectButton.setOnClickListener(v -> connectToKit());
        robotButton.setOnClickListener(v -> {
            Intent intent = new Intent(this, RobotActivity.class);
            if (kitHolster != null) {
                intent.putExtra(RobotActivity.EXTRA_CURRENT_HOLSTER, kitHolster.toString());
            }
            robotLauncher.launch(intent);
        });

        frameView.setVisibility((!settings.isBle() & !settings.isBluetooth()) ? VISIBLE : GONE);

        setConnectedUi(false);

        String instruction = "Say \u201CHey Robot, Connect\u201D.";
        if (!speakMode) instruction = "Press Connect, then Start to begin typing.";
        addStatus("Not connected to kit! " + instruction, VIEW_TYPE_PHONE);

        updateVoiceUi();
        requestRuntimePermissions();
    }

    private void submitMessage() {
        String messageText = messageBox.getText().toString().trim();

        if (!messageText.isEmpty()) {

            handleCommand(messageText);
            messageBox.setText("");
            layoutChatInput.setVisibility(View.GONE);

            InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null) {
                imm.hideSoftInputFromWindow(messageBox.getWindowToken(), 0);
            }
        } else {
            Toast.makeText(this, "Cannot send an empty message", Toast.LENGTH_SHORT).show();
        }
    }

    private void findSpeakMode(){

        this.prefs = this.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);

        speakMode = prefs.getBoolean(SPEAK_MODE, false);


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
        if(DEBUGGING) addStatus("Loading speech model\u2026", VIEW_TYPE_PHONE);

        StorageService.unpack(this, MODEL_ASSET_DIR, "model",
            loadedModel -> {
                model = loadedModel;
                if(DEBUGGING) addStatus("Speech model ready.", VIEW_TYPE_PHONE);
                startRecognition();
            },
            exception -> {
                addStatus("Could not load speech model: " + exception.getMessage(), VIEW_TYPE_PHONE);
                addStatus("Check that assets/" + MODEL_ASSET_DIR
                        + " holds the unzipped model (am/, conf/, graph/\u2026).", VIEW_TYPE_PHONE);
                mode = Mode.OFF;
                updateVoiceUi();
            });
    }

    private void startRecognition() {
        if (speechService != null) return;
        if (model == null || !hasMicPermission()) {
            requestRuntimePermissions();
            return;
        }

        try {
            Recognizer recognizer = new Recognizer(model, SAMPLE_RATE);
            recognizer.setWords(true);
            speechService = new SpeechService(recognizer, SAMPLE_RATE);
            speechService.startListening(this);
            mode = Mode.WAKE;
            Log.d(TAG, "vosk listening");
        } catch (IOException e) {
            addStatus("Could not start the recognizer: " + e.getMessage(), VIEW_TYPE_PHONE);
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

        String text = extractText(hypothesis, "partial");

        if(mode == Mode.WAKE){
            if(!text.isEmpty() && wakePhraseTail(text) != null) beginCommandMode(true);
            return;
        }

        if(mode == Mode.COMMAND){
            String p = normalize(text);
            if(!p.equals(lastPartial) && !isNoise(p)){
                lastPartial = p;
                armCommandTimeout();
            }
        }

//        if (mode == Mode.WAKE) {
//            String text = extractText(hypothesis, "partial");
//            if (text.isEmpty()) return;
//
//            if (wakePhraseTail(text) != null) {
//                beginCommandMode(true);
//            }
//            return;
//        }
//
//        if(mode == Mode.COMMAND && !awaitingWakeCallEnd){
//            armCommandTimeout();
//        }

    }

    @Override
    public void onResult(String hypothesis) {
        lastPartial = "";
        String text = extractText(hypothesis, "text");
        if (text.isEmpty()) return;

        String clean = extractConfidentWords(hypothesis);
        String tail = wakePhraseTail(clean);
        if(tail == null) tail = wakePhraseTail(text);

        Log.d(TAG, "utterance (" + mode + "): " + text);

//        if(awaitingWakeCallEnd){
//            awaitingWakeCallEnd = false;
//            String tail = wakePhraseTail(text);
//            if(tail != null && !tail.isEmpty()){
//                handleCommand(tail);
//                mode = Mode.WAKE;
//                updateVoiceUi();
//            }
//
//            else{
//                armCommandTimeout();
//            }
//
//            return;
//        }
//
//        if (mode == Mode.COMMAND) {
//            cancelCommandTimeout();
//
//            String tail = wakePhraseTail(text);
//            handleCommand(tail != null && !tail.isEmpty() ? tail : text);
//            mode = Mode.WAKE;
//            updateVoiceUi();
//            return;
//        }
//
//        if (mode == Mode.WAKE) {
//            String tail = wakePhraseTail(text);
//            if (tail == null) return;
//            if (!tail.isEmpty()) {
//                // "hey robot, move forward" spoken in one breath
//                handleCommand(tail);
//            } else {
//                beginCommandMode(false);
//            }
//        }
        if(mode == Mode.COMMAND){
            awaitingWakeCallEnd = false;
            if(tail != null) commandBuffer.setLength(0);
            findEnd(tail != null ? tail : clean);
            return;
        }

        if(mode == Mode.WAKE && tail != null){
            beginCommandMode(false);
            if (!tail.isEmpty()) findEnd(tail);
        }
    }

    @Override
    public void onFinalResult(String hypothesis) {}

    @Override
    public void onError(Exception exception) {
        addStatus("Recognizer error: " + exception.getMessage(), VIEW_TYPE_PHONE);
        mode = Mode.OFF;
        updateVoiceUi();
    }

    @Override
    public void onTimeout() {}

    private void findEnd(@NonNull String text){
        String t = normalize(text);
        boolean stop = t.equals(STOP_WORD) || t.endsWith(" " + STOP_WORD);
        String body = stop ? t.substring(0, t.length() - STOP_WORD.length()).trim() : t;

        if(!isNoise(body)){
            if (commandBuffer.length() > 0) commandBuffer.append(' ');
            commandBuffer.append(body);
            if (!stop) armCommandTimeout();
        }

        if(stop) {
            String cmd = commandBuffer.toString().trim();
            commandBuffer.setLength(0);
            endCommandMode();

            if(!cmd.isEmpty()) handleCommand(cmd);
        }
    }

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

         if(!fromPartial) armCommandTimeout();
    }

    private void endCommandMode() {
        cancelCommandTimeout();
        awaitingWakeCallEnd = false;
        if (mode != Mode.COMMAND) return;
        mode = Mode.WAKE;
        lastPartial = "";
        commandBuffer.setLength(0);
        updateVoiceUi();
    }

    private void armCommandTimeout() {

        cancelCommandTimeout();

        long delay = commandBuffer.length() > 0 ? SILENCE_TIMEOUT_MS : COMMAND_TIMEOUT_MS;
        commandTimeoutTask = () -> {
            commandTimeoutTask = null;
            if (commandBuffer.length() > 0){
//                addStatus("Didn't hear \u201Cend\u201D! Discarded: " + commandBuffer, VIEW_TYPE_PHONE);
                String cmd = commandBuffer.toString().trim();
                commandBuffer.setLength(0);
                endCommandMode();
                if(!cmd.isEmpty()) handleCommand(cmd);
            }
            endCommandMode();
        };

        ui.postDelayed(commandTimeoutTask, delay);
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

    private String extractConfidentWords(String json){
        if (json == null) return "";
        try{
            JSONObject o = new JSONObject(json);
            JSONArray words = o.optJSONArray("result");
            if (words == null) return normalize(o.optString("text", ""));

            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < words.length(); i++) {
                JSONObject w = words.getJSONObject(i);
                if(w.optDouble("conf", 1.0) >= MIN_WORD_CONF) {
                    sb.append(w.optString("word", "")).append(' ');
                }
            }
            return normalize(sb.toString());
        } catch (JSONException e){
            return "";
        }
    }

    private boolean isNoise(String text){
        if (text.isEmpty()) return true;
        for (String w : text.split(" ")){
            if (!FILLER.contains(w)) return false;
        }

        return true;
    }

    private void handleCommand(@NonNull String raw) {
        String text = normalize(raw);
        if (text.isEmpty()) return;

        addStatus(text, VIEW_TYPE_YOU);

        if (text.contains("never mind") || text.contains("cancel")) {
            return;
        }
        if (text.contains("connect")) {
            connectToKit();
            return;
        }
        if (kit == null || !kit.isConnected()) {
            addStatus("Not connected to the kit — say \u201Chey robot, connect\u201D first.", VIEW_TYPE_PHONE);
            return;
        }
        kit.sendInstruction(text);
    }

    private void updateVoiceUi() {
        runOnUiThread(() -> {
            if(speakMode){
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
            }
            else{
                startStopButton.setText("Start");
                startStopButton.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, R.color.mh_1)));
            }

        });
    }

    private void onRobotClosed(int resultCode, @Nullable Intent data) {
        if (resultCode != RESULT_OK || data == null) return;
        String[] ops = data.getStringArrayExtra(RobotActivity.EXTRA_CONFIG_OPS);
        if (ops == null || ops.length == 0) return;
        if (kit == null || !kit.isConnected()) {
            addStatus("Not connected to the kit — holster config not sent.", VIEW_TYPE_PHONE);
            return;
        }
        for (String op : ops) {
            kit.sendInstruction(CONFIG_PREFIX + op);
        }
    }

    /** Ask the kit for its holster block; the reply lands in handleConfigReply. */
    private void requestHolster() {
        // BLE interface node doesn't route CONFIG lines yet
        if (kit == null || !kit.isConnected() || settings.isBle()) return;
        try {
            kit.sendInstruction(CONFIG_PREFIX + new JSONObject()
                    .put("op", "get_holster").put("id", "tablet-sync").toString());
        } catch (JSONException e) {
            Log.w("MainActivity", "couldn't build get_holster", e);
        }
    }

    /**
     * If status is a config_editor reply ({"op", "ok", "tools", ...}), cache the holster and
     * show a short result instead of the raw JSON. Returns false for any other status text.
     */
    private boolean handleConfigReply(@NonNull String status) {
        String text = status.trim();
        if (!text.startsWith("{")) return false;
        JSONObject reply;
        try {
            reply = new JSONObject(text);
        } catch (JSONException e) {
            return false;
        }
        if (!reply.has("ok") || !reply.has("tools")) return false;

        JSONObject holster = reply.optJSONObject("tools");
        if (holster != null) kitHolster = holster;

        String message = reply.optString("message", "");
        if (!reply.optBoolean("ok", false)) {
            addStatus("Holster update failed: " + (message.isEmpty() ? "unknown error" : message),
                    VIEW_TYPE_PHONE);
        } else if (!message.isEmpty()) {
            addStatus("Holster: " + message, VIEW_TYPE_PHONE);
        }
        return true;
    }

    /** "<transport>:<address>" for the current Settings; null if that transport's address is unset. */
    @Nullable
    private String settingsTarget() {
        if (settings.isBle()) return "ble:";   // BLE needs no address
        String address = settings.isBluetooth() ? settings.getKitMac() : settings.getKitHost();
        if (address == null || address.trim().isEmpty()) return null;
        return (settings.isBluetooth() ? "bluetooth:" : "wifi:") + address.trim();
    }

    /** Tell the user which address is missing for the selected transport. */
    private void reportMissingAddress() {
        addStatus(settings.isBluetooth()
                        ? "No kit selected. Open Settings and pick the kit's Bluetooth device."
                        : "No kit address set. Open Settings and enter the kit's IP.",
                VIEW_TYPE_PHONE);
        setConnectedUi(false);
    }

    private void onSettingsClosed() {
        String target = settingsTarget();
        if (target == null) {
            reportMissingAddress();
            return;
        }
        // reconnect if the transport or its address changed, or we aren't connected
        if (!target.equals(connectedTarget) || kit == null || !kit.isConnected()) {
            if(DEBUGGING) addStatus("Settings changed. Reconnecting to " + target + "\u2026", VIEW_TYPE_PHONE);
            connectToKit();
        }
    }

    private void connectToKit() {
        String host = settings.getKitHost();
        String type = settings.isBluetooth() ? "Bluetooth"
                : (settings.isBle() ? "BLE" : "WiFi");

        String target = settingsTarget();
        if (target == null) {
            reportMissingAddress();
            return;
        }

        if (kit != null) {
            kit.setListener(null);
            kit.disconnect();
        }

        connectedTarget = target;
        kit = settings.isBluetooth()
                ? new BluetoothKitLink(settings.getKitMac(), settings.getChannel(), this)
                : (settings.isBle() ? new BleKitLink(this) : new TcpClientKitLink(host, KIT_PORT, this));
        kit.setListener(this);

        addStatus("Connecting over " + type + "\u2026", VIEW_TYPE_PHONE);
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
                    addStatus("Could not connect within 30 seconds. Tap Connect to try again.", VIEW_TYPE_PHONE);
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
                addStatus("Connected to kit!", VIEW_TYPE_PHONE);
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
        runOnUiThread(() -> {
            if (!handleConfigReply(status)) addStatus(status, VIEW_TYPE_ROBOT);
        });
    }

    @Override public void onKitConnectionChanged(boolean connected, @NonNull String detail) {
        setConnectedUi(connected);
        runOnUiThread(() -> {
            toast(detail);
            if (connected) {
                requestHolster();
            } else {
                kitHolster = null;   // re-sync on the next connection
            }
        });
    }

    @Override public void onDbgMsg(@NonNull String msg) {
        if(DEBUGGING) runOnUiThread(() -> addStatus("Dbg: " + msg, VIEW_TYPE_PHONE));
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

//    @Override
//    public void onKitCamera(byte[] image){
//
//        String base64Image = Base64.encodeToString(image, Base64.DEFAULT);
//
//        String htmlData = "<html><head><style>"
//                + "body { margin: 0; background-color: #1a1a1a; display: flex; justify-content: center; align-items: center; height: 100vh; }"
//                + "img { width: 100%; height: 100%; object-fit: contain; }"
//                + "</style></head><body>"
//                + "<img src='data:image/png;base64,"
//                + base64Image
//                + "' />"
//                + "</body></html>";
//
//        runOnUiThread(new Runnable() {
//            @Override
//            public void run() {
////                frameView.loadDataWithBaseURL("file:///android_asset/", htmlData, "text/html", "utf-8", "");
//            }
//        });
//    }

    private void addStatus(@NonNull String text, int fromYou) {
        runOnUiThread(() -> {
            ChatMessage newMessage = new ChatMessage(text, fromYou);
            messageList.add(newMessage);
            chatAdapter.notifyItemInserted(messageList.size() - 1);
            recyclerView.scrollToPosition(messageList.size() - 1);
        });
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
            if(speakMode) loadModel();
        } else {
            permissionLauncher.launch(needed.toArray(new String[0]));
        }
    }
}

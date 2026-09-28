# Architecture

This repo turns natural-language instructions into motion on a Kinova Gen3 arm with a Robotiq 2F-140 gripper and a wrist-mounted RealSense. It is a ROS 2 colcon workspace split into three layers:

1. **Robot layer** (C++): `kortex_controller` owns the connection to the arm and exposes a few motion primitives as ROS 2 actions.
2. **Agent layer** (Python): `gemini_agent` runs a Gemini Live session, gives the model camera images and robot state, and turns its function calls into action goals.
3. **User layer**: the browser UI (`web/index.html`) and the phone-app transports (`app_interface`) that feed instructions in and show what the agent is doing.

All three layers talk only over ROS 2 topics and actions, so each can be run, restarted, or replaced on its own.

## System diagram

```
  Browser (web/index.html)        Phone app
        │  rosbridge :9090             │  BLE / RFCOMM / TCP :9100
        │                              ▼
        │                     app_interface node
        │                              │
        ├──────── /user_instructions ◄─┘      (std_msgs/String)
        ├──────── /stop                       (std_msgs/Empty)
        ◄──────── /agent/events               (JSON in std_msgs/String)
        ◄──────── /agent/model_image          (CompressedImage)
        ◄──────── /robot_state
        │
        ▼
  gemini_agent (agent_node) ◄────────► Gemini Live API (streaming)
   ├─ Session      : live connection, turn handling, interruption
   ├─ tools/       : look, move_to, move_relative, move_straight, ...
   ├─ perception/  : Gemini detector ─► SAM2 masks ─► depth + TF ─► base-frame xyz
   ├─ Camera       : synced color/depth + intrinsics + TF snapshots
   └─ Robot        : action clients + latest RobotState
        │  actions: move_to_pose, move_to_joints, move_straight, command_gripper
        │  topics : /stop, /robot_state
        ▼
  kortex_controller (C++) ───── Kortex TCP ─────► Kinova Gen3
        │  publishes /robot_state, /joint_states
        ▼
  robot_state_publisher ─► TF (base_link … camera_link ─► physical_realsense_link)
  realsense2_camera     ─► /camera/realsense/{color, aligned_depth_to_color}/...
```

## Packages

### `ros2_interfaces`: the robot API

The contract between the robot layer and everything above it.

| Interface | Kind | Purpose |
|---|---|---|
| `MoveToPose` | action | Move the end effector to a 6-DoF pose (m, Kortex extrinsic XYZ Euler degrees) |
| `MoveToJoints` | action | Move to a 7-joint configuration (degrees) |
| `MoveStraight` | action | Move a distance along the tool's own Z axis (+ forward, − back) |
| `CommandGripper` | action | Set the gripper, 0 = open to 1 = closed |
| `ServoToTarget` | action | Visual servoing toward a moving target on `/servo_vector` (declared, not implemented) |
| `ComputeIK` | service | Pose to joint solution |
| `RobotState` | msg | Cartesian pose, 7 joint angles, gripper position |

Every action result carries `success` and `message`, which the agent passes straight through to the model.

### `kortex_controller`: the robot layer

One `rclcpp::Node` (`src/kortex_controller/src/controller.cpp`) that opens a Kortex TCP session to the arm and hosts the action servers above, the `compute_ik` service, and a 50 ms timer that publishes state.

**Concurrency.** Every Kortex call goes through one mutex, `mApiMutex`. Each accepted goal runs on its own detached thread and polls the arm until it arrives, times out, is canceled, or is stopped. The state timer also takes the mutex.

**Motion primitives.**
- `MoveToPose` solves IK with Kortex, seeded with the current joint angles, then executes the result as a joint-space move. A joint-space move gives smoother motion than a Cartesian move and keeps the orientation.
- `MoveToJoints` polls joint angles until all are within 1°, handling 360° wraparound, with a 30 s timeout.
- `MoveStraight` computes the tool Z axis in the base frame from the reported Euler angles (`toolZAxis` / `shiftAlongToolZ`), sends a Cartesian target, and polls with `pollUntilCartesianTarget` (1 cm tolerance, 5 s timeout).
- `CommandGripper` sends a position command. Reaching the numeric target is not required for success, because the gripper can stop against a grasped object before it gets there.

**Stopping.** A message on `/stop` calls `Base::Stop()` right away (under the mutex) and sets `mStopRequested`, which every polling loop checks. Accepting a new goal clears the flag.

**State outputs.**
- `/robot_state` (`RobotState`) is what the agent and the UI read.
- `/joint_states` (`sensor_msgs/JointState`, radians, plus `finger_joint` = gripper × 0.7) lets `robot_state_publisher` build the TF chain through the arm. Without it, TF splits into two trees and camera-to-base projection fails.

### `gemini_agent`: the agent layer

A Python package with two entry points: `agent_node` (the live agent) and `tool_cli` (runs one tool from the terminal, without the model).

#### Threading model

`agent_node.main()` splits the work across two threads:

- A background thread spins a `MultiThreadedExecutor` for all ROS I/O: subscriptions, action client futures, and TF.
- The main thread runs an `asyncio` event loop for the Gemini session and tool execution.

ROS callbacks never touch asyncio objects directly. They hand work over with `loop.call_soon_threadsafe`, and `Robot._await_rclpy_future` bridges rclpy futures into asyncio futures. Blocking work (the Gemini detector call, SAM2 inference) runs in `asyncio.to_thread`.

SAM2 is loaded with `segmenter.preload()` **before** `rclpy.init()`. Once the camera subscriptions are live, they hold the GIL often enough that importing torch goes from seconds to minutes.

#### Modules

| Module | Responsibility |
|---|---|
| `config.py` | Finds `config.yaml` (`$KINOVA_GEMINI_CONFIG`, else by walking up from the package to the repo root), loads `.env`, resolves the system prompt path |
| `robot.py` | `Robot`: one action client per primitive, latest `RobotState`, `stop()`, and a compact `state()` dict the model sees after every action |
| `camera.py` | `Camera`: approximately time-synced color/depth pairs, intrinsics, and `base_frame ← projection_frame` TF. `fresh_snapshot(after=t)` waits for a frame newer than `t`, so post-motion images are never stale |
| `session.py` | `Session`: the Gemini Live connection and the core loop (below) |
| `tools/` | Tool registry and the tool implementations |
| `perception/` | Pipeline behind the `look` tool |
| `agent_node.py` | Wires everything together and publishes UI events and images |
| `tool_cli.py` | Same wiring, but runs one tool and prints its JSON result |

#### Session loop

`Session.run()` connects to the Live API (text responses, optional thinking, sliding-window context compression, session resumption) and runs two coroutines side by side:

- **`instruction_loop`** takes the next user instruction off a queue, cancels any running tool, captures a fresh frame, and sends the image followed by `USER: <text>\nROBOT: ee=(x, y, z) gripper=g`.
- **`receive_loop`** handles server messages: text and thoughts become UI events, `tool_call` starts `run_tools` as a task, and `go_away` or an error triggers a reconnect using the stored resumption handle.

**`run_tools`** runs the model's function calls in order. When they finish, it sends one image (the annotated `look` image if a tool produced one, otherwise a fresh post-motion frame) and then the function responses. A `send_lock` keeps an image and the text or response that belongs with it from interleaving with another send.

**Interruption.** A new user message or a `/stop` while a tool is running cancels the tool task. `Robot._send_goal` catches the `CancelledError`, cancels the ROS goal, and re-raises. The tool then responds with `status: "interrupted"` (later calls in the batch get `"skipped"`). The response goes out before the new user turn, because the Live API expects every open function call to be answered first.

#### Tools

A tool is an async function `fn(ctx: ToolContext, **args) -> dict` registered with the `@tool(name, description, parameters)` decorator. `config.yaml`'s `tools:` list decides which ones the model is offered. `ToolContext` bundles `robot`, `camera`, `cfg`, `emit`, `publish_image`, and the `genai_client`, so the same tools run under `agent_node` or `tool_cli`.

| Tool | Does |
|---|---|
| `look(objects)` | Finds named objects and returns their base-frame centroids plus an annotated image |
| `move_to(x, y, z)` | `MoveToPose` to a base-frame point, keeping the current orientation |
| `move_relative(dx, dy, dz)` | Same as `move_to`, relative to the current pose |
| `move_straight(distance)` | `MoveStraight` along the gripper axis |
| `set_gripper(position)` | `CommandGripper` |
| `go_to_preset(name)` | `MoveToJoints` to a preset from `config.yaml` (the enum is filled in from config) |
| `stop()` | Publishes `/stop` and cancels open goals |
| `move_to_pose(...)` | Raw 6-DoF pose (off by default; turning it on also exposes orientation in `state()`) |

Every result is a JSON-able dict with `ok`, `status`, optional `message`, and a `robot` state snapshot. Keys starting with `_` (such as `_image`) are stripped before the dict goes to the model.

#### Perception (`look`)

`perception.locate_objects(snapshot, names)` works in three stages, each swappable:

1. **`detector.detect_boxes`**: a non-streaming Gemini call (`perception.detector_model`) returns `box_2d` boxes normalized to 0–1000, one per visible instance.
2. **`segmenter.segment`**: SAM2 (local checkout, not pip-installed) turns each box into its highest-scoring mask.
3. **`localize.centroid`**: deprojects the mask's valid depth pixels with the intrinsics, transforms them into the base frame with the snapshot's TF, and averages them.

Objects get ids like `red cup#1` that are valid only for that call. `localize.annotate` draws mask outlines and ids onto the image, which is sent back to the model so it can check its own detections.

Because coordinates are in the base frame, they stay valid after the camera moves. The system prompt (`prompts/system.md`) tells the model to measure once with `look` and plug the result into the motion tools.

#### Observability

`agent_node` publishes every step to `/agent/events` as JSON (`{t, type, data}`, with types such as `session`, `user_text`, `model_text`, `model_thought`, `tool_call`, `tool_result`, `look`, `image`, `turn_complete`, `error`). It also publishes every image the model receives on `/agent/model_image`, with `header.frame_id` set to the image id. `Session.send_image` is the only path images take to the model, so the UI shows exactly what the model saw.

### `app_interface`: phone transports

Three interchangeable nodes, one per transport. Each receives newline-delimited text instructions from a phone app and republishes them on `/user_instructions`:

- `ble_interface`: BLE central (bleak) that scans for a phone advertising a Nordic-UART-style service. Outgoing status is chunked into `n/total:` frames (`ble_framing.py`).
- `bluetooth_interface`: classic Bluetooth RFCOMM server on channel 1. It calls libc directly because Anaconda's Python lacks `AF_BLUETOOTH` support.
- `network_interface`: TCP server on port 9100 (same Wi-Fi network).

Each node also forwards `/brain_status` strings back to the phone. The current agent does not publish `/brain_status`; it reports through `/agent/events`.

### `kinova_bringup`: launch

- **`robot.launch.py`** starts `kortex_controller`, `robot_state_publisher` (URDF from `kortex_description` via xacro), the RealSense driver (aligned depth on), two static transforms from `camera_link` (`realsense_link` for point-cloud display, and `physical_realsense_link`, the frame `look` projects through), rosbridge, web_video_server, and optionally RViz.
- **`agent.launch.py`** includes `robot.launch.py`, adds `gemini_agent`, and starts one `app_interface` transport chosen with `app:=`.

### `web/index.html`: operator UI

A single static page using roslibjs over rosbridge. It shows a timeline built from `/agent/events` (user and model bubbles, collapsible thoughts, tool calls and results), attaches `/agent/model_image` frames to their events by image id, shows live `/robot_state`, and has an instruction box (`/user_instructions`) and a stop button (`/stop`).

## Configuration

All runtime behavior is in `config.yaml` at the repo root:

- `model`: Live model name, thinking level, whether thoughts are included, system prompt path, context compression
- `tools`: which tools the model gets
- `perception`: detector model, SAM2 repo, config, checkpoint, device, and whether the annotated image is sent
- `camera`: topics, projection and base frames, image send width and JPEG quality, settle time after motion, sync slop
- `presets`: named joint configurations (degrees)
- `ros`: topic names used by the agent

`GEMINI_API_KEY` is read from `.env` at the repo root.

## Design choices

- **Primitive actions, reasoning in the model.** The controller exposes only a few motion primitives. Task logic (look, approach, grasp, verify) lives in the model's tool calls, guided by `prompts/system.md`.
- **Base-frame coordinates everywhere.** Perception returns base-frame points and the motion tools accept them, so the model never deals with camera frames.
- **An image after every action.** The model gets a fresh frame after each tool batch and each user turn, so it can check what actually happened instead of trusting the tool's `ok` status.
- **Stop at two levels.** `/stop` halts the arm directly in C++ without waiting on Python, and also cancels the agent's running tool.
- **Tools don't depend on the model.** Tools depend only on `ToolContext`, so `tool_cli` can test each one on hardware without a Gemini session.

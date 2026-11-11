# Kinova Gen3 x Gemini Live

Enables natural-language control of a Kinova Gen3 arm. A user states a task, and a Gemini Live session completes it by composing a library of robot skills.

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for more details.

## Packages

| Package | Language | What it does |
|---|---|---|
| `src/ros2_interfaces` | msg/srv/action | Custom ROS 2 actions, service, and `RobotState` message |
| `src/kortex_controller` | C++ | Talks to the arm over the Kortex API and exposes it as ROS 2 actions (the robot primitives) |
| `src/gemini_agent` | Python | Gemini Live session, skills (`look`, `pick`, `move_to`, `attach`, ...), and perception (Gemini boxes + SAM2 masks + depth) |
| `src/app_interface` | Python | Phone-app transports (BLE, Bluetooth RFCOMM, TCP) that publish instructions |
| `src/kinova_bringup` | launch | Launch files for the robot stack and the full agent |
| `web/index.html` | HTML/JS | Browser UI: chat, model thoughts, skill calls, images the model saw, camera stream, robot state, stop button |

## Requirements

- ROS 2 with `realsense2_camera`, `kortex_description`, `robot_state_publisher`, `rosbridge_server`, `web_video_server`, `cv_bridge`, `message_filters`, `tf2_ros`
- Kinova Kortex C++ API, unpacked into `src/kortex_controller/kortex_api/` (`include/` and `lib/release/libKortexApiCpp.a`; not checked in)
- Python deps for the agent: `google-genai`, `python-dotenv`, `pyyaml`, `ruamel.yaml`, `opencv-python`, `numpy`, `torch`, `mediapipe`
- The MediaPipe face landmarker model at `models/face_landmarker.task` (not checked in; used by the `itch` skill):
  `mkdir -p models && curl -fsSL -o models/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`
- A local [SAM2](https://github.com/facebookresearch/sam2) checkout with a checkpoint (path set in `config.yaml` under `perception`)
- A CUDA GPU (for SAM2) and a Gemini API key

## Setup

```bash
# 1. API key
cp .env.example .env   # then put your key in .env

# 2. Build (from the repo root)
colcon build --symlink-install
source install/setup.bash

# optional - ensure entry points point to conda env
sed -i '1s|.*|#!/home/mcrr-lab/venvs/kinova-gemini/bin/python3|' install/{gemini_agent/lib/gemini_agent,app_interface/lib/app_interface}/*
```

Edit `config.yaml` for your setup: model, enabled skills, SAM2 paths, camera topics, preset joint positions, and the `tools:` map of each tool the robot will use to its slot's ArUco marker id and its length.

## Running

Full stack (controller, camera, TF, rosbridge, and the Gemini agent):

```bash
ros2 launch kinova_bringup agent.launch.py robot_ip:=192.168.77.10
```

Then open `web/index.html` in a browser (it connects to rosbridge on `ws://localhost:9090`).

Launch arguments: `robot_ip`, `username`, `password` (default `192.168.77.10` / `admin` / `admin`), `rviz:=true`, and `app:=ble|bluetooth|network|none` to start a phone transport.

Robot stack only, without the agent:

```bash
ros2 launch kinova_bringup robot.launch.py
```

### Running a single skill by hand

`skill_cli` calls one skill without the model, which is useful for testing motion and perception:

```bash
ros2 run gemini_agent skill_cli --list
ros2 run gemini_agent skill_cli move_relative '{"dz": 0.03}'
ros2 run gemini_agent skill_cli look '{"objects": ["red cup"]}'
ros2 run gemini_agent skill_cli pick '{"x": 0.45, "y": 0.0, "z": 0.1, "width_mm": 60, "depth_mm": 70}'
ros2 run gemini_agent skill_cli attach '{"name": "fork"}'
ros2 run gemini_agent skill_cli itch '{"location": "left_cheek"}'
```

Ctrl-C cancels the running robot goal.

### Stopping the arm

Publish to `/stop` (the web UI's stop button does this) to halt the arm immediately:

```bash
ros2 topic pub --once /stop std_msgs/msg/Empty
```

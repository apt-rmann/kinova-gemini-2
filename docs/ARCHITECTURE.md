# Architecture

We use a vision-language model (VLM) to compose a library of robot skills to complete a user-defined task. Our repo follows the **primitive → skill → task** framework from [Pedersen et al.](https://www.sciencedirect.com/science/article/pii/S0736584515000575)

## Framework

```
                    ┌──────────────────────────────┐
   User ──────────► │  TASK      "put the toy in   │   what the user wants done
                    │             the box"         │
                    └──────────────┬───────────────┘
                                   │  composed at runtime by the
                                   │  Gemini Live session
                    ┌──────────────▼───────────────┐
                    │  SKILLS    look · pick ·     │   parameterized, object-centered
                    │  move_to · attach · ...      │   abilities
                    └──────────────┬───────────────┘
                                   │  each skill is a fixed composition of
                    ┌──────────────▼───────────────┐
                    │  PRIMITIVES  MoveToPose ·    │   single robot operations
                    │  MoveStraight · Gripper ·... │   (ROS 2 actions)
                    └──────────────────────────────┘
```

| Layer | What it is here |
|---|---|
| **Primitive** | One ROS 2 action on the controller: a single, atomic robot operation that reports `success` and `message` |
| **Skill** | An async Python function that combines primitives (and sensing) into one ability with a few intuitive parameters, and reports what happened |
| **Task** | Whatever the user asks for. Not written ahead of time; it exists only as the sequence of parameterized skill calls the operator chooses |
| **Operator** | The Gemini Live model. It reads the request, looks at the camera image and robot state, picks skills, fills in their parameters, and checks the result after each one |


## Primitives

Primitives are ROS2 actions in `kortex_controller`, a C++ node that holds the Kortex connection to the arm and moves the arm using the Kortex API. 

| Primitive | Kind | Does |
|---|---|---|
| `MoveToPose` | action | Move the end effector to a 6-DoF pose (IK, then a joint-space move) |
| `MoveToJoints` | action | Move to a 7-joint configuration |
| `MoveStraight` | action | Move a distance along the gripper's own Z axis (+ forward, − back) |
| `CommandGripper` | action | Set the gripper opening, 0 = open to 1 = closed |
| `ServoToTarget` | action | Uses Twist commands to drive the TCP towards a target location` |
| `SetToolLength` | service | Move the TCP a distance along tool +Z past the bare gripper, to the tip of an attached tool |
| `/stop` | topic | Halt the arm immediately and abort any running primitive |
| `/robot_state` | topic | Current pose, joint angles, gripper position, tool length |

**Tool TCP.** The TCP is the Kortex tool frame, so every primitive and the reported pose follow it with no per-primitive code. The controller writes the bare-gripper TCP (`bare_tool_z`, 0.22 m) to the arm at startup. `attach` then extends it by the tool's hand-measured `length` in `config.yaml`'s `tools:` block (which also gives each tool's holster marker id), and `detach` resets it to the bare gripper before seating the tool.

`ComputeIK` (service) is declared but not used by any skill yet.

Primitives don't know about objects, tasks, or the model. They can be tested on their own with `ros2 action send_goal`, and any skill can use them.

## Skills

A skill is one decorated async function in `src/gemini_agent/gemini_agent/skills/`:

```python
@skill(name='pick', description='...', parameters={...JSON schema...})
async def pick(ctx: SkillContext, x, y, z, width_mm, depth_mm) -> dict:
    ...
```

- The **declaration** (name, description, parameter schema) is what the operator sees. It is sent to Gemini as a function declaration.
- The **body** calls primitives through `ctx.robot` and sensing through `ctx.camera` and the perception pipeline.
- The **result** is a dict with `ok`, `status`, an optional `message`, and a `robot` state snapshot. Keys that start with `_` (such as `_image`) are kept out of what the model sees.

`config.yaml`'s `skills:` list chooses which skills the operator is offered. `skill_cli` runs any skill from the terminal without the model, which is how skills are tested on hardware.


### Skill library

| Skill | Primitives it composes | Purpose |
|---|---|---|
| `look(objects)` | camera snapshot → Gemini detector → SAM2 → depth + TF | Finds named objects and returns their base-frame centroids, grasp width, and estimated depth, plus an annotated image |
| `pick(x, y, z, width_mm, depth_mm)` | `CommandGripper` → `MoveToPose` → `MoveStraight` → `CommandGripper` | Opens just wider than the object, moves to it, advances to its middle, and closes onto it |
| `attach(name)` / `detach(name)` | `MoveToJoints` → (`CommandGripper`) → ArUco detection → `MoveToPose` → `MoveStraight` → `CommandGripper` → `MoveStraight` | Takes a physical tool from its holster slot, or returns it |
| `move_to(x, y, z)` | `MoveToPose` | Moves to a base-frame point, keeping the current orientation |
| `move_relative(dx, dy, dz)` | `MoveToPose` | Same, relative to the current position |
| `move_straight(distance)` | `MoveStraight` | Moves along the gripper axis |
| `set_gripper(position)` | `CommandGripper` | Opens or closes the gripper |
| `go_to_preset(name)` | `MoveToJoints` | Moves to a named joint configuration from `config.yaml` |
| `stop()` | `/stop` | Halts the arm |
| `move_to_pose(...)` | `MoveToPose` | Raw 6-DoF pose (off by default) |

Some skills wrap a single primitive. They give the model a way to fill gaps between the larger skills (lifting after a grasp, releasing over a target). 

Some implementation notes on the larger skills:

## Tasks

A task is whatever the user asks for during the session:

```
 user ──"put the toy in the box"──► Session ──image + "USER: … ROBOT: ee=… gripper=…"──► Gemini
                                                                                      │
                     ┌───────────────── plan, then function calls ◄───────────────────┘
                     ▼
              run each skill ──► primitives on the arm
                     │
                     └──► skill results + fresh image ──► Gemini checks, then calls next skill … done
```

For example, the operator might turn *"put the toy in the box"* into:

| Skill call | Parameters come from |
|---|---|
| `look(["toy", "box"])` | the user's words |
| `pick(X1, Y1, Z1, w1, d1)` | `look`'s result for the toy |
| `move_to(X1, Y1, Z1 + 0.15)` | the toy's position, raised |
| `move_to(X2, Y2, Z1 + 0.15)` | the box's X/Y at the same safe height |
| `set_gripper(0.0)` | — |

`Session` (`session.py`) runs this loop over a Gemini Live connection:

- **User turn.** Each instruction is sent with a fresh camera image and a one-line robot state.
- **Skill batch.** The model's function calls run in order. Afterwards it receives one image (the annotated `look` image if there is one, otherwise a fresh frame) and the results. 
- **Interruption.** A new user message or `/stop` cancels the running skill. Its primitive goal is canceled, the skill reports `status: "interrupted"` (any later calls in the batch report `"skipped"`), and the model decides from the new message and image whether to continue, re-plan, or wait.
- **Long sessions.** Context compression and session resumption keep the session going across many image-heavy turns and server reconnects.

The operator's behavior is set in `prompts/system.md`,

## System layout

```
  Browser (web/index.html)      Phone app
        │ rosbridge                 │ BLE / RFCOMM / TCP
        │                     app_interface
        ├── /user_instructions ◄────┘
        ├── /stop
        ◄── /agent/events, /agent/model_image, /robot_state
        ▼
  gemini_agent ◄──────────► Gemini Live API          OPERATOR + SKILLS
        │ ROS 2 actions + /stop
        ▼
  kortex_controller ──── Kortex TCP ───► Kinova Gen3   PRIMITIVES
  robot_state_publisher, realsense2_camera ─► TF, color + depth
```

| Package | Role |
|---|---|
| `ros2_interfaces` | Primitive interfaces (actions, service, `RobotState`) |
| `kortex_controller` | Primitive implementations (C++) |
| `gemini_agent` | Skills (`skills/`), perception, and the operator session (`session.py`, `agent_node.py`) |
| `app_interface` | Phone transports that publish user tasks to `/user_instructions` |
| `kinova_bringup` | `robot.launch.py` (primitives, camera, TF, rosbridge) and `agent.launch.py` (adds the operator) |
| `web/index.html` | Shows each user turn, model thought, skill call and result, and every image the model saw|

The layers communicate only over ROS 2 topics and actions, so each one can be restarted or replaced on its own. `agent_node` publishes every step to `/agent/events` (`user_text`, `model_thought`, `skill_call`, `skill_result`, `look`, `holster`, ...) and every image sent to the model to `/agent/model_image`, so the UI shows exactly what the model saw.


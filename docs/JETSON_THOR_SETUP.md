# Jetson AGX Thor setup

Step-by-step setup for running the whole `kinova-gemini` stack on a Jetson AGX Thor instead of
the laptop. Follow the steps in order; each one ends with a **Check** that must pass before you
move on.

Allow a full day the first time, most of it downloads and the §11 bring-up tests.

- Steps 1–4 need a monitor, keyboard and mouse on the Thor, at your bench.
- Steps 5–12 are done over SSH from your laptop.
- After step 12 the Thor runs headless. Nothing is reinstalled to make that happen — you just
  unplug the monitor.

Decisions already made, so you don't have to re-litigate them mid-install (reasons in
Appendix B): **no Docker**, **no conda — a venv instead**, **upstream Meta SAM2, not the
`SAM2_streaming` fork**, and **SSH from your laptop** as the way you work and deploy.

---

## Before you start

Have these on the bench:

- [ ] Jetson AGX Thor devkit + its 140 W USB-C supply
- [ ] HDMI or DisplayPort monitor, USB keyboard, USB mouse
- [ ] **USB-A** flash drive, 16 GB+ (USB-C sticks have been reported to fail in the installer)
- [ ] Short Cat6 cable for the arm
- [ ] A **USB 3** cable for the RealSense (a USB-2 cable makes the camera fail or drop frames)
- [ ] Your lab Wi-Fi name and password
- [ ] Your laptop, on the same Wi-Fi
- [ ] For deployment day: a **USB-A→Ethernet adapter** (~$15) — see step 13

How things are wired, for reference throughout:

```
            ┌──────────── Wi-Fi ────────────────────► internet (Gemini API)
            │                                     └─► your laptop / the tablet app
      ┌─────┴──────────────────┐
      │   Jetson AGX Thor      │── RJ45 ────────────► Kinova Gen3 @ 192.168.77.10
      │                        │── USB-A ───────────► RealSense D4xx
      │                        │── USB-C ───────────► 140 W PSU
      └────────────────────────┘── HDMI ──────────── monitor (bench only)
```

The arm takes the Thor's only Ethernet port, so **the Thor's internet comes over Wi-Fi**. The
agent needs internet (the Gemini Live session and the detector are both cloud calls), so
whatever Wi-Fi the robot is sitting next to — your lab now, a participant's home later — is a
hard dependency of the robot.

Values this document assumes — change them consistently if you choose differently:

| | Value |
|---|---|
| Thor username | `mcrr-lab` |
| Thor hostname | `thor` (so `thor.local` works) |
| Repo | `~/kinova-gemini-2` |
| venv | `~/venvs/kinova-gemini` |
| SAM2 | `~/sam2` |
| Arm IP | `192.168.77.10` (moved off the factory `192.168.1.10` — see §3.2) |
| Thor wired IP | `192.168.77.11` |

---

## 1. Flash JetPack and do the first boot

Needs the monitor, a keyboard and a mouse. The Thor ships without a usable OS on its SSD: you
write an installer to a USB stick, boot the Thor from that stick, and the installer puts Jetson
Linux onto the internal NVMe. Budget an hour, including the Ubuntu wizard at the end.

You do **not** need a host Ubuntu PC, SDK Manager, or force-recovery mode for this. Your laptop
is only used to write the USB stick, so Windows or macOS would work too.

> There is also a monitor-free version of this install, over the **Debug-USB** serial console —
> but that port is behind the lid cover, the wizard runs as text screens, and NVIDIA lists
> display bugs on the factory UEFI. Since you have a monitor, use it. Do this step at your
> bench, never on deployment day.

### 1.1 Write the installer stick (on your laptop)

1. Go to [NVIDIA's JetPack downloads page](https://developer.nvidia.com/embedded/jetpack/downloads)
   and download the **Jetson ISO** for AGX Thor — the current release is JetPack 7.2.1
   (Jetson Linux r39.2.1). You need ~25 GB free on the laptop.
2. Install [balenaEtcher](https://etcher.balena.io/) and write the ISO to the USB stick:
   select the ISO → select the USB stick → Flash. It erases the stick.

   **Do not drag-and-drop the ISO onto the stick.** It must be *written as an image*; a copied
   file will not boot and this is the single most common way to lose an hour here.
3. Use a **USB-A stick** if you have one. NVIDIA says either a Type-A or Type-C port works, but
   there are reports of USB-C sticks erroring out partway through the install.

Note which JetPack version you downloaded — step 6 needs its CUDA version. JetPack 7.2.x ships
CUDA 13.2, so you will most likely want the `cu132` PyTorch index; JetPack 7.0 shipped CUDA 13.0
(`cu130`). You will confirm this with `nvcc --version` in step 2 regardless.

### 1.2 Plug everything in (on the Thor)

With the Thor **powered off**:

- Monitor → HDMI or DisplayPort. Connect the monitor **directly**; some KVM switches show no
  video on this board.
- USB keyboard and mouse → a USB-A port (use a hub if you only have one free).
- Installer stick → the other USB-A port.
- Bundled 140 W USB-C supply → a USB-C port. Use the bundled supply; a random USB-C charger may
  not deliver enough power. One of the two USB-C ports is the power sink and the other is
  force-recovery, so if pressing power does nothing at all, move the cable to the other USB-C
  port.
- **Leave the Ethernet cable and the RealSense unplugged** for now. Fewer variables.

Then press the **power button on the left-hand side** of the chassis.

### 1.3 Boot from the stick

The factory boot order already prefers an attached USB stick over the NVMe, so in the normal
case you do nothing here.

1. Watch the monitor. The NVIDIA logo appears, then a **pre-boot options** screen. Either wait
   for its timeout or press **Enter** to boot from the USB stick.
2. **If it boots past that into something else** (or you want to confirm the order): power
   cycle, and this time press **Esc repeatedly** while the NVIDIA logo is on screen. That opens
   the UEFI setup. Go to **Boot Manager**, move the USB stick to the top of the list, then
   choose **Save & Exit**. It reboots and comes back to the pre-boot screen.
3. **If you are prompted about a QSPI capsule update, press `Y`.** Do not skip it. The update
   runs **twice** and reboots the board each time — this is expected, let it finish. If you
   miss the prompt or dismiss it, start the installation over.
4. A **GRUB menu** appears. Choose **Install on NVMe** (the recommended option).

### 1.4 Let the installer run

The screen fills with scrolling white text. **It will look stalled for minutes at a time — that
is normal.** Do not power cycle it. The install takes a little under 10 minutes, then the board
reboots on its own.

**Pull the USB stick out before that reboot completes.** If the stick is still in, the board
boots the installer again instead of your fresh install. (If that happens, no harm: power off,
remove the stick, power on.)

### 1.5 Ubuntu's first-run wizard (oem-config)

The board now boots from the NVMe into a GUI setup wizard. Work through the screens:

Welcome → keyboard layout → **NVIDIA driver licence: Accept** → Network (you can **Skip** it;
step 3 does the networking properly) → time zone → About You → password → Setup Complete.

On the **About You** screen use these, since the rest of this document assumes them:

- Your name: anything
- Computer's name (hostname): **`thor`** — this is what makes `thor.local` work in step 4
- Username: **`mcrr-lab`**
- Password: something you **write down now**. You will need it for every `sudo` and once more
  in step 4.

Click **Start Using Ubuntu**. It finishes installing and lands you on the desktop.

**Check:**

```bash
whoami          # mcrr-lab
hostname        # thor
```

### Troubleshooting step 1

| Symptom | Fix |
|---|---|
| Nothing on the monitor at all | connect the monitor directly, not through a KVM |
| No power, no fans, no logo | move the USB-C power cable to the other USB-C port; use the bundled supply |
| Boots to an existing/previous OS, ignoring the stick | Esc at the logo → Boot Manager → USB stick to the top → Save & Exit |
| The stick isn't listed in Boot Manager | it wasn't written as an image — reflash it with balenaEtcher |
| Black screen after the install starts, on a board that was used before | a UEFI **SOC Display Hand-Off** setting; see NVIDIA's "Re-enable USB stick installation" page |
| It booted the installer a second time | the stick was still inserted; power off, remove it, power on |
| Install appears frozen on scrolling text | expected — wait the full 10 minutes before suspecting anything |

---

## 2. Base system: JetPack, power mode, clock

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y nvidia-jetpack        # CUDA, cuDNN, TensorRT — large, ~20 min
sudo reboot
```

After the reboot, put it at full performance. Jetson caps itself by power mode, and this
machine lives on wall power, so there is no reason to run it throttled:

```bash
sudo nvpmodel -q                # list modes and show the current one
sudo nvpmodel -m 0              # mode 0 = MAXN (confirm the number from the list above)
sudo jetson_clocks              # optional: pin clocks high
```

Set the clock. Timestamps matter here: `camera.sync_slop_s` pairs color and depth frames by
timestamp, and TF lookups are time-based.

```bash
sudo timedatectl set-timezone America/New_York
timedatectl                     # want: "System clock synchronized: yes"
```

**Check:**

```bash
nvidia-smi                      # or: tegrastats
cat /etc/nv_tegra_release       # records your L4T/JetPack version — note it down
nvcc --version                  # note the CUDA version; you need it in step 6
```

**`nvcc: command not found` does not mean the install failed.** CUDA installs to
`/usr/local/cuda/`, which is not on the PATH by default. Add it once:

```bash
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
nvcc --version
```

Keep the `LD_LIBRARY_PATH` line — some pip packages look for `libcudart` at runtime and fail
without it. If `ls /usr/local/cuda*/bin/nvcc` finds nothing at all, then the toolkit really is
missing: `sudo apt install -y cuda-toolkit`. Either way, the CUDA version step 6 needs is also
printed in the `nvidia-smi` header and is in the `/usr/local/cuda-*` directory name.

---

## 3. Networking: Wi-Fi for the internet, a fixed address for the arm

Still at the monitor. There is one thing to configure here, and one thing to click.

### 3.1 Wi-Fi: just use the desktop

Click the Wi-Fi icon in the top-right of the Ubuntu desktop, pick your network, type the
password. That is all. Ubuntu saves it and rejoins it automatically on every boot, and you can
save as many networks as you like — the Thor joins whichever one it can see. Nothing in this
document depends on which network it is.

**While you are here, also turn on your phone's hotspot and join that too.** It takes a minute
and it is what gets you back into a headless Thor on a network it has never seen (step 13).

### 3.2 Wired: give the Thor an address on the arm's network

Plug the Cat6 cable from the Thor into the arm. Then:

```bash
ip -br link
```

Find the line that is `UP` **and** says `LOWER_UP` — that means a live cable is attached, so it
is the RJ45 with the arm on it. On this Thor it is **`enP2p1s0`**. Watch out for `wlP1p1s0`,
which is the Wi-Fi: the names are confusingly similar, and only the `en`/`wl` prefix tells them
apart. The `mgbe*` entries are SoC MAC blocks with no connector on the devkit — all
`NO-CARRIER`, ignore them.

Then, substituting your interface name, **as a single line** (a `\` followed by a space is not
a line continuation, and nmcli will reject the arguments):

```bash
sudo nmcli connection add type ethernet ifname enP2p1s0 con-name kinova-arm ipv4.method manual ipv4.addresses 192.168.77.11/24 connection.autoconnect yes
```

**Why this is needed.** Normally a computer plugs into a router and the router *hands it* an
address. The arm is not a router — it is a device at one fixed address that hands out nothing.
So if you leave the Thor on "get an address automatically," it waits, gets nothing, and gives
itself a junk address in an unrelated range. The arm is then unreachable even though the cable
is fine.

The command above says: on this cable, don't wait for anyone, just *be* `192.168.77.11`. That
address is the Thor's own, chosen to sit next to the arm's `.10` on the same network. The
`/24` means "the first three numbers are the neighbourhood" — `192.168.77.anything` is local,
everything else goes out over Wi-Fi.

Note there is no gateway in that command, and that is deliberate: a gateway is how you say
"send internet traffic this way," and the arm is not a route to the internet. Leaving it blank
is what keeps the Wi-Fi as the only way out.

**Check:**

```bash
ping -c2 192.168.77.10          # the arm answers
ping -c2 1.1.1.1                # the internet answers
```

If both answer, networking is done.

### 3.3 Why the arm is on 192.168.77.x and not its factory address

The Gen3 ships at `192.168.1.10`, and the first build of this setup kept it there. **Do not.**
`192.168.1.x` is the most common address range for consumer routers, and this already bit us in
the lab: the lab Wi-Fi handed the Thor `192.168.1.155`, so the Wi-Fi and the arm cable both
claimed `192.168.1.x` at the same time.

What that looks like when it happens, so you recognise it: the Thor has internet and the arm
pings locally, but **SSH from the laptop dies** — the laptop can find the Thor at the hardware
level yet gets no reply to anything. The Thor is receiving your packets and sending the answers
down the arm cable, because two interfaces claim the same range and the cable wins on route
metric. Address lookup still works because that always answers on the interface that asked;
replies go through the route table, which is where it breaks.

Moving the arm to a range nothing hands out by default removes the whole class of problem, at
the lab and at a participant's house. Change it in the arm's own web interface (browse to its
current address, log in `admin`/`admin`, find the network settings), set `192.168.77.10`, save,
and power-cycle the arm. Then update the Thor's side to match, or you will be unable to reach
the arm at either address:

```bash
sudo nmcli connection modify kinova-arm ipv4.addresses 192.168.77.11/24
sudo nmcli connection up kinova-arm
```

If you ever lose the arm completely after an address change, give the Thor's cable both ranges
at once so you can reach it either way, then narrow it back down:

```bash
sudo nmcli connection modify kinova-arm ipv4.addresses 192.168.77.11/24,192.168.1.11/24
```

And to find the arm's real address when the web form may not have taken: run
`sudo tcpdump -i enP2p1s0 -n -c 30`, power-cycle the arm, and read the source address off its
boot traffic.

**Check the collision really is gone** — with the arm cable plugged in, from the laptop:

```bash
ssh mcrr-lab@thor.local hostname
```

That failing while the cable is connected, and working when it is unplugged, is the signature.

---

## 4. SSH in from your laptop

This is how you do everything from here on. You run a terminal on your laptop that executes
commands on the Thor.

On the **Thor** (last thing you type at the monitor):

```bash
sudo apt install -y openssh-server avahi-daemon tmux
sudo systemctl enable --now ssh
```

`avahi-daemon` is what makes the name `thor.local` work, so you don't have to chase a changing
IP address.

On your **laptop**:

```bash
ssh-keygen -t ed25519           # only if you have never made a key; press Enter through it
ssh-copy-id mcrr-lab@thor.local # type the Thor's password once — the last time you ever will
ssh mcrr-lab@thor.local         # from now on: straight in
```

**Check:** `ssh mcrr-lab@thor.local hostname` prints `thor` without asking for a password.

Set these up now, they pay for themselves immediately:

- **VS Code Remote-SSH** on your laptop — install the "Remote - SSH" extension, connect to
  `mcrr-lab@thor.local`, and you get the Thor's files in your editor and its terminal in the
  panel. This is the recommended way to work (see Appendix B).
- **`tmux`** — run `tmux` on the Thor before launching anything long-running, so the stack
  survives your SSH session dropping. `tmux attach` to get back to it. Without this, closing
  your laptop lid kills the agent.

You can leave the monitor plugged in at the bench — useful for RViz and for rescue if the
network itself breaks — but do your work over SSH.

---

## 5. ROS 2 Jazzy and the ROS dependencies

Everything below is over SSH. JetPack 7 is Ubuntu 24.04, so this is the same ROS distro as the
laptop and every package comes from apt as an arm64 deb.

```bash
sudo apt install -y software-properties-common curl
sudo add-apt-repository -y universe
export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F '"tag_name"' | awk -F\" '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo $VERSION_CODENAME)_all.deb"
sudo apt install -y /tmp/ros2-apt-source.deb
sudo apt update

sudo apt install -y \
  ros-jazzy-desktop ros-dev-tools \
  ros-jazzy-librealsense2 ros-jazzy-realsense2-camera \
  ros-jazzy-kortex-description ros-jazzy-robotiq-description ros-jazzy-robot-state-publisher \
  ros-jazzy-rosbridge-suite ros-jazzy-web-video-server \
  ros-jazzy-cv-bridge ros-jazzy-message-filters ros-jazzy-tf2-ros ros-jazzy-xacro

echo 'source /opt/ros/jazzy/setup.bash' >> ~/.bashrc
source ~/.bashrc
```

**Check 1:** `ros2 topic list` runs and prints `/parameter_events` and `/rosout`.

**Check 2** — `kortex_description` comes from apt here, but from a source workspace on the
laptop (`~/workspace/ros2_kortex_ws`), so confirm the released xacro accepts the arguments
`robot.launch.py` passes:

```bash
xacro $(ros2 pkg prefix kortex_description)/share/kortex_description/robots/kinova.urdf.xacro \
  robot_ip:=192.168.77.10 name:=kinova arm:=gen3 gripper:=robotiq_2f_140 dof:=7 \
  vision:=true use_fake_hardware:=false > /tmp/kinova.urdf && echo OK
```

**If it fails with a missing `.xacro` file**, the released `kortex_description` is pulling in a
sibling package that apt did not install with it. The first one is `robotiq_description` (hence
it being in the list above); the error names the file, and the owning package is the
`ros-jazzy-<package>-description` for whatever directory it sits in. Install it and re-run.

This is the one thing in step 5 genuinely worth checking rather than assuming: the laptop has
never run the released `kortex_description`. It builds from source in `~/workspace/ros2_kortex_ws`,
which also checks out `ros2_robotiq_gripper`, so those siblings were always present there and
never had to be declared. The deb makes them external dependencies.

If the chain turns out to be deeper than one or two packages, stop fighting apt: copy the
`kortex_description` and `robotiq_description` source checkouts from the laptop's
`ros2_kortex_ws/src` into `~/kinova-gemini-2/src/` and let them build with the rest in step 10.
That also guarantees the Thor runs the same description the laptop has been tested against.

---

## 6. Python environment and PyTorch

No conda on the Thor. NVIDIA's PyTorch wheels are built for the system CPython 3.12, which is
also exactly what Jazzy's `rclpy` is built against, so a venv that can see the system packages
is both simpler and more robust.

```bash
sudo apt install -y python3-venv python3-pip
python3 -m venv --system-site-packages ~/venvs/kinova-gemini
echo 'source ~/venvs/kinova-gemini/bin/activate' >> ~/.bashrc   # must come AFTER the ROS line
source ~/.bashrc
```

**Check:** `python -c "import rclpy; print('rclpy ok')"` — this is what `--system-site-packages`
buys you. If it fails, the venv was created without that flag; delete it and redo.

**PyTorch.** Match the index URL to the CUDA version you noted in step 2 (`nvcc --version`):
CUDA 13.0 → `cu130`, CUDA 13.2 → `cu132`.

```bash
pip install --upgrade pip
pip install torch torchvision --extra-index-url https://download.pytorch.org/whl/cu130
```

**Check — do not continue past this one:**

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

It must print `True`. SAM2 is useless on CPU here and `perception.device` is `cuda`. A common
failure is a missing `libcudart.so.13`, fixed by installing the matching CUDA runtime package
from the Jetson apt repo.

**The rest of the Python dependencies:**

```bash
pip install google-genai python-dotenv pyyaml opencv-contrib-python \
            hydra-core iopath pillow tqdm bleak
```

`opencv-contrib-python`, not plain `opencv-python`: `perception/aruco.py` uses `cv2.aruco`,
which is a contrib module, and the holster skills need it.

**Check:** `python -c "import cv2; cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50); print('aruco ok')"`

> If you later hit a NumPy-2 error from SAM2 or cv2, pin it with `pip install "numpy<2"` (the
> laptop runs 1.26.4; the Jetson torch wheels tend to pull 2.x).

---

## 7. SAM2 (upstream Meta, not the fork)

`segmenter.py` only calls `build_sam2` and `SAM2ImagePredictor`, which are plain upstream API,
so nothing in this repo depends on the `SAM2_streaming` fork.

```bash
cd ~ && git clone https://github.com/facebookresearch/sam2.git
cd ~/sam2
pip install -e .                # venv must be active
# only the tiny checkpoint (149 MB); download_ckpts.sh would fetch all four (~3 GB)
curl -L -o checkpoints/sam2.1_hiera_tiny.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
```

`pip install -e .` tries to compile an optional CUDA extension (`sam2._C`). **If that fails the
install still succeeds, and that is fine** — build errors are tolerated by default, and the
extension is only used for a mask fill-holes post-process this repo never calls. Don't chase it.

**Check:** `python -c "import sam2; print('sam2 ok')"`

> Switching from the fork to upstream changes two strings in `config.yaml`, because the two
> repos register their Hydra config module differently. Step 9 handles it; don't skip it, or
> SAM2 fails with a confusing Hydra error.

---

## 8. The Kortex C++ API, aarch64 build

The copy on your laptop is x86 and **will not link** on the Thor. Download the aarch64 build of
the same version this repo uses (2.7.0):

```bash
mkdir -p ~/dl && cd ~/dl
curl -LO https://artifactory.kinovaapps.com/artifactory/generic-public/kortex/API/2.7.0/linux_aarch64_gcc_7.4.zip
mkdir -p kortex_aarch64 && unzip -q linux_aarch64_gcc_7.4.zip -d kortex_aarch64
ls kortex_aarch64                        # expect include/ and lib/
```

Copy it into the package (do this after step 9's clone, or create the directory first):

```bash
mkdir -p ~/kinova-gemini-2/src/kortex_controller/kortex_api
cp -r ~/dl/kortex_aarch64/include ~/dl/kortex_aarch64/lib \
      ~/kinova-gemini-2/src/kortex_controller/kortex_api/
```

`CMakeLists.txt` needs no change — it just requires these two paths to exist:
`kortex_api/include` and `kortex_api/lib/release/libKortexApiCpp.a`. If the zip's internal
layout differs, move files so those paths exist rather than editing CMake.

**Check:**

```bash
file ~/kinova-gemini-2/src/kortex_controller/kortex_api/lib/release/libKortexApiCpp.a
grep PROJECT_VER ~/kinova-gemini-2/src/kortex_controller/kortex_api/include/common/version.h
```

---

## 9. The repo, the API key, and `config.yaml`

```bash
cd ~ && git clone https://github.com/jakmilller/kinova-gemini-2.git
```

Two things are deliberately not in git. The API key, copied from your **laptop**:

```bash
scp ~/kinova-gemini-2/.env mcrr-lab@thor.local:~/kinova-gemini-2/.env
```

And the Kortex API from step 8, if you haven't copied it in yet.

Now edit `~/kinova-gemini-2/config.yaml`. The `perception:` block is the only part that needs
changing — all three of these lines:

```yaml
perception:
  sam2_repo: /home/mcrr-lab/sam2                        # was .../raf-live/SAM2_streaming
  sam2_config: configs/sam2.1/sam2.1_hiera_t.yaml       # upstream needs the configs/ prefix
  sam2_checkpoint: checkpoints/sam2.1_hiera_tiny.pt     # upstream has no sam2.1/ subdir
```

Why each one: the fork keeps `configs/` at its root and calls
`initialize_config_module("configs")`; upstream keeps configs at `sam2/configs/` and calls
`initialize_config_module("sam2")`, so the name needs the prefix. And upstream's checkpoint
script writes to `checkpoints/` flat, not `checkpoints/sam2.1/`. `sam2_repo` is still required
even though SAM2 is pip-installed — `segmenter.py` reads it to put the checkout on `sys.path`,
which is now redundant but harmless.

`sam2_repo` is the only absolute path in the whole runtime config. Everything else resolves
relative to the checkout (`config.py` walks up from the package to find `config.yaml`), so the
repo can live anywhere.

**Check:** `grep -A4 '^perception:' ~/kinova-gemini-2/config.yaml` and `test -s ~/kinova-gemini-2/.env && echo "env ok"`

---

## 10. Build

```bash
cd ~/kinova-gemini-2
source /opt/ros/jazzy/setup.bash
source ~/venvs/kinova-gemini/bin/activate     # order matters: venv active when colcon runs
colcon build --symlink-install
source install/setup.bash
echo 'source ~/kinova-gemini-2/install/setup.bash' >> ~/.bashrc
```

Expect this to take longer than on the laptop; the C++ controller against the Kortex static lib
is the slow part. `colcon` builds `ros2_interfaces` first on its own.

Because the venv was active during the build, the generated `agent_node` / `skill_cli` shebangs
already point into the venv — **the `sed` shebang hack in `README.md` is a conda workaround and
must not be run here.**

**Check:**

```bash
head -1 install/gemini_agent/lib/gemini_agent/agent_node   # should point into ~/venvs/kinova-gemini
ros2 run gemini_agent skill_cli --list                      # prints the enabled skills
```

---

## 11. Bring-up tests, one layer at a time

Do not launch the full stack first. Each step below has an obvious pass/fail; skipping ahead
turns three small problems into one confusing one.

Per `CLAUDE.md`, **you run everything that moves the robot.** Hand on the e-stop for steps 5–6.

**11.1 — camera alone.** Is USB 3 and the RealSense healthy on this machine?

```bash
ros2 launch realsense2_camera rs_launch.py align_depth.enable:=true pointcloud.enable:=true camera_name:=realsense
# in a second SSH session / tmux pane:
ros2 topic hz /camera/realsense/color/image_raw                       # want ~30
ros2 topic hz /camera/realsense/aligned_depth_to_color/image_raw      # want ~30
```

If the rate is low or the camera isn't found, suspect the USB cable before anything else.

**11.2 — arm alone.** Does the aarch64 Kortex library really talk to the robot?

```bash
ros2 run kortex_controller controller --ros-args \
  -p robot_ip:=192.168.77.10 -p username:=admin -p password:=admin
ros2 topic echo /robot_state --once
ros2 topic echo /joint_states --once        # CLAUDE.md trap 3: TF splits in two without this
```

**11.3 — the robot stack** (controller + camera + TF + rosbridge + web_video_server):

```bash
ros2 launch kinova_bringup robot.launch.py robot_ip:=192.168.77.10
ros2 run tf2_ros tf2_echo base_link physical_realsense_link
```

That last command must print a transform. A `LookupException` on the first try is expected and
covered by `CLAUDE.md` trap 2 — latched `/tf_static` takes ~3 s to reach a new process.

**11.4 — perception on the GPU**, with no model in the loop:

```bash
ros2 run gemini_agent skill_cli look '{"objects": ["red cup"]}'
```

Watch the log for `loaded sam2.1/... on cuda in Ns`. **Note that N.** If it is ever minutes
rather than seconds, `segmenter.preload()` has stopped running before `rclpy.init()` —
`CLAUDE.md` trap 1. That trap is a GIL problem, not an x86 one, so it applies here unchanged,
and the Thor's individual CPU cores are slower than the Legion's, so the symptom is worse.

**11.5 — motion**, small and deliberate:

```bash
ros2 run gemini_agent skill_cli move_relative '{"dz": 0.03}'
ros2 run gemini_agent skill_cli go_to_preset '{"name": "home"}'
```

**11.6 — the whole stack:**

```bash
ros2 launch kinova_bringup agent.launch.py robot_ip:=192.168.77.10
```

Then make the web UI change in Appendix A.1 and open it from your laptop's browser.

**11.7 — re-teach the physical values.** `presets:` and the `holster:` marker→tool map in
`config.yaml` describe where things are in the room, not anything about the computer. They need
re-teaching at the deployment site regardless of this port. Verify `/stop` and the web UI's
stop button work before you rely on any of it.

---

## 12. Make it start on its own (going headless)

Your SSH + `tmux` session is how you work while you are there. This service is for after you
leave, when a power cut or a reboot has to bring the robot back without you. You want both.

Create `/etc/systemd/system/kinova-agent.service`:

```ini
[Unit]
Description=Kinova Gemini agent stack
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mcrr-lab
WorkingDirectory=/home/mcrr-lab/kinova-gemini-2
Environment=HOME=/home/mcrr-lab
Environment=ROS_DOMAIN_ID=0
# wait for the arm's own boot (the Gen3 takes tens of seconds) and for Wi-Fi
ExecStartPre=/bin/sleep 15
ExecStart=/bin/bash -lc 'source /opt/ros/jazzy/setup.bash && source /home/mcrr-lab/venvs/kinova-gemini/bin/activate && source install/setup.bash && exec ros2 launch kinova_bringup agent.launch.py robot_ip:=192.168.77.10 app:=network'
Restart=no
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable kinova-agent      # start at every boot
sudo systemctl start kinova-agent       # start now
```

Two deliberate choices in that file, worth revisiting once you trust it:

- `Restart=no` to begin with. `Restart=on-failure` will relaunch the stack after a crash, which
  also means it can re-home an arm that stopped for a reason. Watch `journalctl` for a week
  first, then switch it to `on-failure` if you want self-healing.
- `ExecStartPre=/bin/sleep 15` is a crude wait. If the controller fails on cold boot but works
  on `systemctl restart`, lengthen it — or better, replace it with a loop that pings
  `192.168.77.10` until it answers.

**Check — do this twice in a row before you call the Thor ready:** full power cycle, nothing
plugged in but power, Ethernet, camera and Wi-Fi, and the stack comes up by itself.

```bash
sudo reboot
# wait, then from the laptop:
ssh mcrr-lab@thor.local systemctl status kinova-agent
```

**Now unplug the monitor.** That is the entire act of "going headless."

---

## 13. Deployment day

On site, your plan is the same as your bench workflow: laptop and Thor on the same Wi-Fi,
`ssh mcrr-lab@thor.local`, work in `tmux`, read errors on your own screen.

**The one new problem is the first five minutes.** The Thor arrives headless and knows none of
the networks at that address, so it joins nothing — and you can't SSH in to tell it the new
password, because telling it the password requires being in. You need one way in that doesn't
depend on the local Wi-Fi.

Pick whichever of these you prefer; the first is the lightest:

- **Your phone hotspot.** Turn it on and the Thor joins it by itself, because you saved it back
  in §3.1. Join your laptop to the same hotspot, `ssh mcrr-lab@thor.local`, add the local
  Wi-Fi, done. This also doubles as the robot's internet if the local Wi-Fi turns out to be
  unusable.
- **The USB-A→Ethernet adapter.** The arm owns the Thor's only built-in RJ45, so this adapter
  is a second wired port: laptop straight into it, SSH over the cable. Test it at the bench
  first so you aren't debugging the adapter on site.
- **A monitor and keyboard.** Bulky, but it always works.

Bring:

- [ ] your phone (hotspot saved on the Thor in §3.1) — and/or the USB-A→Ethernet adapter
- [ ] the local Wi-Fi name and password, confirmed in advance
- [ ] a monitor + keyboard, or know you can borrow a TV with HDMI, as last resort

On arrival, add the local Wi-Fi. Easiest is the desktop Wi-Fi menu if you brought a monitor;
over SSH it's one command:

```bash
sudo nmcli device wifi connect "<network name>" password "<password>"
ping -c2 1.1.1.1                              # the Thor has internet
ssh mcrr-lab@thor.local                       # from the laptop, now on that same Wi-Fi
ip -br addr                                   # sanity: Wi-Fi address is not 192.168.1.x (§3.3)
```

Then re-teach `presets:` and `holster:` for the new room (§11.7), run §11's ladder on site, and
do the power-cycle check from §12 in place.

Before you leave:

- [ ] `/stop` and the web UI stop button tested **with the participant present**
- [ ] tablet app connects to the Thor's address on port 9100
- [ ] power-cut test passed on site: pull the plug, restore, the stack returns by itself
- [ ] the participant and whoever is with them know how to cut power, and who to call

Optional, ten minutes, do it at the bench: install Tailscale so a future visit isn't the only
way to check on the robot. You don't need it for this deployment, but wanting it after you've
driven home costs a second trip.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh         # prints a URL; open it and log in
tailscale ip -4                 # a 100.x.y.z address that works from anywhere
```

If you leave it enabled, tell the participant the computer can be reached remotely for support,
and that no images or audio leave the house except the images the agent sends to Gemini — which
is already true today on the laptop.

---

## Appendix A: code changes this port needs

Nothing in the agent, the controller, or the launch files is x86-specific. Four small items:

### A.1 The web UI's hardcoded `localhost` — **required**

`web/index.html:183` connects to `ws://localhost:9090` and `web/index.html:113` streams from
`http://localhost:8080`. That was fine when the browser and ROS were the same machine. With the
Thor headless, the browser is on your laptop or the tablet, and `localhost` is wrong.

Fix: derive the host from where the page was loaded, and serve the page off the Thor —
`python3 -m http.server 8000 -d web`, or add that to the service. Then `http://thor.local:8000`
works from any device on the network with nothing hardcoded. A few lines; ask me to do it.

### A.2 `config.yaml`'s `perception:` paths — **required**

Covered in step 9. The three SAM2 lines, of which `sam2_repo` is the only absolute path in the
runtime config.

### A.3 The conda `sed` hack in `README.md` — should go

It exists only because the agent runs under conda on the laptop. With the venv in step 6 it is
unnecessary and actively misleading. Worth replacing with the venv instructions once the Thor
works, so both machines follow one documented path.

### A.4 Deployment hardening — before the house, not before first boot

- **What `session.py` does when the Gemini Live socket dies.** On home Wi-Fi a dropped
  connection is routine, not exceptional. If the agent exits, the service restarts it and the
  arm is fine. If it hangs, the robot looks dead and nobody in the room can tell which it is.
- **`/brain_status`** — `CLAUDE.md` notes the app nodes forward it but nothing publishes it. In
  the house the tablet is the participant's only feedback channel, so an empty status channel
  matters much more than it does on your bench.
- **Persistent logs.** `journalctl` covers this; just confirm `/var/log/journal` exists so you
  can answer "what happened at 4pm Tuesday."

---

## Appendix B: why the setup looks like this

**Why this port is mostly mechanical.** JetPack 7 is Ubuntu 24.04 with Python 3.12, so ROS 2
Jazzy installs from apt and every ROS package this stack uses is published as an arm64 deb.
Only three things are architecture-specific, and all three have arm64 builds: the Kortex C++
API (step 8), PyTorch (step 6), and the ROS debs themselves (step 5). Everything else is
architecture-independent Python and JavaScript.

**Why no Docker.** It would buy a reproducible CUDA/torch environment and a fast path to a
second unit. It would cost: USB device access for the RealSense inside the container (and a
stale device mapping if the camera is ever replugged — in a house, "replug the camera" must be
a fix, not a new problem); the dev loop you have today, where a Python edit needs no rebuild at
all thanks to `--symlink-install`; and one more layer to debug on top of ROS, DDS, two
networks, a cloud API and a robot arm. The usual argument for containers is dependency
conflicts, and there are none here because the whole machine is dedicated to one application.
For reproducibility, do the cheap version once everything works:

```bash
pip freeze > docs/thor-pip-freeze.txt
apt list --installed | grep ros-jazzy > docs/thor-apt-ros.txt
```

Commit those and this document becomes a repeatable build. Revisit Docker at unit #2 or #3.

**Why a venv and not conda.** NVIDIA's PyTorch wheels are built for the system CPython 3.12,
which is the same interpreter Jazzy's `rclpy` is built against. `venv --system-site-packages`
therefore gets both in one environment with no shebang rewriting.

**Why upstream SAM2.** `segmenter.py` uses only `build_sam2` and `SAM2ImagePredictor`, so the
fork buys nothing here, and upstream is maintained and pip-installable.

**Why "headless" needs no special install.** The Thor runs ordinary Ubuntu. A monitor only
gives it somewhere to draw; with no monitor the same OS boots the same services. The only
things headless operation actually requires are a way in over the network (step 4) and
something to start the stack that isn't you typing (step 12).

**Why develop over SSH rather than at the monitor.** For terminal work they are identical —
same filesystem, same processes, and no meaningful speed difference, since anything slow
(`colcon build`, SAM2 loading) is the Thor's CPU either way. SSH wins on two counts: your
editor, browser and the operator web UI all stay on one screen, and you exercise the
deployment-day path every single day, so you find its problems at the lab. Keep the monitor for
RViz (`ssh -X` works but is sluggish) and for rescue when the network is what's broken.

---

## Appendix C: troubleshooting

| Symptom | Likely cause |
|---|---|
| `torch.cuda.is_available()` is `False` | wrong wheel index for your CUDA version (step 6); or missing `libcudart.so.13` |
| `import rclpy` fails inside the venv | venv made without `--system-site-packages` |
| SAM2 fails with a Hydra config error | `sam2_config` missing the `configs/` prefix (step 9) |
| `cv2.aruco` missing | plain `opencv-python` installed instead of `opencv-contrib-python` |
| Controller links but won't connect to the arm | wired interface config, or the arm still booting (§12's `sleep`) |
| `tf2_echo` raises `LookupException` | expected for ~3 s (`CLAUDE.md` trap 2); if persistent, check `/joint_states` (trap 3) |
| TF splits into two trees, projections fail | `/joint_states` is not being published (trap 3) |
| SAM2 load time jumps from seconds to minutes | `segmenter.preload()` no longer runs before `rclpy.init()` (trap 1) |
| `Failed init_port fastrtps_port7010` | a suspended (Ctrl-Z'd) ROS process is holding DDS ports; kill it (trap 6) |
| Web UI connects on the Thor but not the laptop | the hardcoded `localhost` in `web/index.html` (A.1) |
| Agent dies when you close your laptop | you launched outside `tmux` |
| SSH dies whenever the arm cable is plugged in | Wi-Fi and the arm cable claim the same range; check `ip -br addr` (§3.3) |
| Arm unreachable at both old and new address after a change | the Thor's cable was not moved to the new range (§3.3) |
| `nmcli`: `invalid or not allowed setting ' ipv4'` | a `\` line continuation got pasted with a trailing space; run the command on one line |
| `nvcc: command not found` | CUDA is installed, just not on the PATH (step 2) |
| Everything throttles under load | power mode not MAXN, or no airflow in the enclosure (step 2) |

---

## Appendix D: everyday commands

```bash
# get in
ssh mcrr-lab@thor.local
tmux attach || tmux

# run it by hand
ros2 launch kinova_bringup agent.launch.py robot_ip:=192.168.77.10

# the service
systemctl status kinova-agent
journalctl -u kinova-agent -f          # live log; this replaces watching a terminal
sudo systemctl restart kinova-agent    # the whole deploy loop, for Python-only changes
sudo systemctl stop kinova-agent

# single skills, no model in the loop
ros2 run gemini_agent skill_cli --list
ros2 run gemini_agent skill_cli look '{"objects": ["red cup"]}'

# stop the arm
ros2 topic pub --once /stop std_msgs/msg/Empty

# rebuild (needed only for C++ / ros2_interfaces changes)
cd ~/kinova-gemini-2 && colcon build --symlink-install && source install/setup.bash

# machine health
tegrastats
sudo nvpmodel -q
```

---

## Sources

- [JetPack SDK downloads and release notes](https://developer.nvidia.com/embedded/jetpack/downloads)
- [Jetson AGX Thor Developer Kit — Quick Start](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/quick_start.html)
- [Jetson AGX Thor Developer Kit — Hardware Layout](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/0.1.0/hardware_layout.html)
- [Introducing NVIDIA Jetson Thor](https://developer.nvidia.com/blog/introducing-nvidia-jetson-thor-the-ultimate-platform-for-physical-ai/)
- [PyTorch on Jetson Thor (NVIDIA Developer Forums)](https://forums.developer.nvidia.com/t/pytorch-on-jetson-thor/351933)
- [Installing PyTorch for Jetson Platform](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)
- [Kinova Kortex API downloads (architecture-specific)](https://github.com/Kinovarobotics/kortex)
- [A Gen3 C++ driver using the aarch64 Kortex API on a Jetson](https://github.com/rammp-org/kinova-gen3-driver)
- [Meta SAM2 — install and config-name conventions](https://github.com/facebookresearch/sam2)
- [Tailscale install](https://tailscale.com/download/linux)

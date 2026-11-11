"""Start the robot stack from a phone transport node when the app connects.

Each interface node owns one StackLauncher. When the app
connects, this checks the ROS graph for the stack's nodes; if none exist it
runs `ros2 launch kinova_bringup agent.launch.py app:=none ...` 

A stack started by this launcher is stopped with SIGINT (aka Ctrl-C) when the
interface node shuts down. A stack that was already running is left alone.

Progress goes back to the app through the `notify(text)` callback the node passes in.
"""

import os
import shutil
import signal
import subprocess
import threading
import time

STACK_NODES = ('gemini_agent', 'kinova_controller')
AGENT_NODE = 'gemini_agent'
INSTRUCTION_TOPIC = '/user_instructions'

DISCOVERY_GRACE_S = 3.0
POLL_S = 0.5
SHUTDOWN_WAIT_S = 20.0


class StackLauncher:

    def __init__(self, node, notify):
        self._node = node
        self._notify = notify
        self._log = node.get_logger()

        self._auto_launch = node.declare_parameter('auto_launch', True).value
        self._robot_ip = node.declare_parameter('robot_ip', '192.168.77.10').value
        self._username = node.declare_parameter('username', 'admin').value
        self._password = node.declare_parameter('password', 'admin').value
        self._rviz = node.declare_parameter('rviz', False).value
        self._ready_timeout = node.declare_parameter('stack_ready_timeout_s', 180.0).value

        self._created = time.monotonic()
        self._lock = threading.Lock()
        self._proc = None       # the ros2 launch process, if this launcher started one
        self._ready = False
        self._stopping = False

    def ensure_running(self):
        """Start the stack if it isn't up."""
        if not self._auto_launch:
            return
        threading.Thread(target=self._ensure_running, daemon=True).start()

    def shutdown(self):
        """Stop the stack this launcher started (no-op if it didn't start one)."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return

        self._log.info('Stopping the robot stack this node launched...')
        self._signal(proc, signal.SIGINT)
        try:
            proc.wait(timeout=SHUTDOWN_WAIT_S)
        except subprocess.TimeoutExpired:
            self._log.warn('Robot stack did not exit after SIGINT, killing it')
            self._signal(proc, signal.SIGKILL)
            proc.wait()
        self._log.info('Robot stack stopped')

    def _ensure_running(self):
        wait = DISCOVERY_GRACE_S - (time.monotonic() - self._created)
        if wait > 0:
            time.sleep(wait)

        with self._lock:
            if self._stopping:
                return

            if self._proc is not None and self._proc.poll() is None:
                # already launched by an earlier connection
                self._notify('Robot ready.' if self._ready
                             else 'Robot stack is still starting, please wait...')
                return
            self._proc = None

            running = self._running_stack_nodes()
            if running:
                self._log.info(f'Stack already running ({", ".join(running)}), not launching')
                return

            ros2 = shutil.which('ros2')
            if ros2 is None:
                self._log.error('Cannot launch the robot stack: `ros2` is not on PATH')
                self._notify('Could not start the robot: ros2 not found on the kit.')
                return

            cmd = [
                ros2, 'launch', 'kinova_bringup', 'agent.launch.py', 'app:=none',
                f'robot_ip:={self._robot_ip}',
                f'username:={self._username}',
                f'password:={self._password}',
                f'rviz:={"true" if self._rviz else "false"}',
            ]
            self._log.info('App connected and no robot stack running; launching: '
                           + ' '.join(['ros2'] + [a for a in cmd[1:] if 'password' not in a]))
            try:
                # own process group
                self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                              start_new_session=True)
            except OSError as e:
                self._log.error(f'Failed to launch the robot stack: {e}')
                self._notify('Could not start the robot stack, check the kit terminal.')
                return
            self._ready = False
            proc = self._proc

        self._notify('Starting the robot, this can take a minute...')
        self._watch(proc)

    def _watch(self, proc):
        """Report readiness."""
        started = time.monotonic()
        warned = False
        while True:
            code = proc.poll()
            if code is not None:
                with self._lock:
                    stopping = self._stopping
                    if self._proc is proc:
                        self._proc = None
                if not stopping:
                    self._log.error(f'Robot stack exited (code {code})')
                    self._notify(f'Robot stack stopped unexpectedly (code {code}). '
                                 'Reconnect to restart it.')
                return

            if not self._ready and self._agent_listening():
                self._ready = True
                self._log.info('Robot stack ready')
                self._notify('Robot ready.')

            if (not self._ready and not warned
                    and time.monotonic() - started > self._ready_timeout):
                warned = True
                self._log.warn(f'Robot stack not ready after {self._ready_timeout:.0f}s')
                self._notify('The robot is taking longer than usual to start')

            time.sleep(POLL_S)

    def _running_stack_nodes(self):
        names = {name for name, _ns in self._node.get_node_names_and_namespaces()}
        return [n for n in STACK_NODES if n in names]

    def _agent_listening(self):
        try:
            infos = self._node.get_subscriptions_info_by_topic(INSTRUCTION_TOPIC)
        except Exception:
            return False
        return any(i.node_name == AGENT_NODE for i in infos)

    @staticmethod
    def _signal(proc, sig):
        try:
            os.killpg(proc.pid, sig)   # start_new_session: pgid == launch pid
        except ProcessLookupError:
            pass

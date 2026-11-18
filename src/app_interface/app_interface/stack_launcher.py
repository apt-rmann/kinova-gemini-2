"""Start and stop the robot stack from a phone transport node.

Each interface node owns one StackLauncher. When the app connects, this checks the ROS
graph for the stack's nodes; if none exist it runs
`ros2 launch kinova_bringup agent.launch.py app:=none ...`

The app can stop the stack ("STACK stop" -> stop()). An instruction that arrives while the
agent isn't listening is held, the stack is started, and the instruction is published once
the agent is ready.

A stack started by this launcher is stopped with SIGINT (aka Ctrl-C) when the interface
node shuts down or the app asks. A stack that was already running is left alone.

Progress goes back to the app through the `notify(text)` callback.
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
STACK_PREFIX = 'STACK '   # app lines for the launcher, e.g. "STACK stop"

DISCOVERY_GRACE_S = 3.0
POLL_S = 0.5
SHUTDOWN_WAIT_S = 20.0
ROBOT_READY = 'Robot ready.'   # MainActivity re-syncs its config when it sees this


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
        self._proc = None           
        self._ready = False
        self._closing = False       # interface node is shutting down
        self._user_stopped = None   # app asked to stop 
        self._stop_done = threading.Event()   # cleared while a stop is in progress
        self._stop_done.set()
        self._held = None           # waiting for the agent to be ready

    def ensure_running(self):
        """Start the stack if it isn't up."""
        if not self._auto_launch:
            return
        threading.Thread(target=self._ensure_running, daemon=True).start()

    def submit_instruction(self, text, publish):
        if not self._auto_launch or self._agent_listening():
            publish(text)
            return
        with self._lock:
            replaced = self._held is not None
            self._held = (text, publish)
        if replaced:
            self._log.info('Replacing the held instruction with a newer one')
        self._notify('Your instruction will be sent when the robot is ready.')
        self.ensure_running()

    def stop(self):
        threading.Thread(target=self._stop, daemon=True).start()

    def shutdown(self):
        """Stop the stack this launcher started"""
        with self._lock:
            self._closing = True
            self._held = None
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._terminate(proc)

    def _stop(self):
        with self._lock:
            self._held = None
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                proc = None
            if proc is not None:
                if self._user_stopped is proc:
                    return   # already being stopped
                self._user_stopped = proc
                self._stop_done.clear()

        if proc is None:
            if self._running_stack_nodes():
                self._notify('The robot was started outside the app; stop it from the kit.')
            else:
                self._notify('The robot is already stopped.')
            return

        self._notify('Stopping the robot...')
        try:
            self._terminate(proc)
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
                self._ready = False
            self._stop_done.set()
        self._notify('Robot stopped.')

    def _terminate(self, proc):
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

        # a stop in progress finishes 
        self._stop_done.wait(SHUTDOWN_WAIT_S + 5.0)

        with self._lock:
            if self._closing:
                return

            if self._proc is not None and self._proc.poll() is None:
                # already launched by an earlier connection
                ready = self._ready
                if not ready:
                    self._notify('Robot stack is still starting, please wait...')
            else:
                ready = None
                self._proc = None
        if ready:
            self._notify(ROBOT_READY)
            self._flush_held()
            return
        if ready is not None:
            return

        with self._lock:
            if self._closing:
                return
            running = self._running_stack_nodes()
            if running:
                self._log.info(f'Stack already running ({", ".join(running)}), not launching')
            else:
                proc = self._launch()
                if proc is None:
                    return
        if running:
            # started outside the app; send a held instruction once its agent listens
            threading.Thread(target=self._wait_external_agent, daemon=True).start()
            return

        self._notify('Starting the robot, this can take a minute...')
        self._watch(proc)

    def _launch(self):
        """Start `ros2 launch` (call with the lock held). Returns the process or None."""
        ros2 = shutil.which('ros2')
        if ros2 is None:
            self._log.error('Cannot launch the robot stack: `ros2` is not on PATH')
            self._notify('Could not start the robot: ros2 not found on the kit.')
            return None

        cmd = [
            ros2, 'launch', 'kinova_bringup', 'agent.launch.py', 'app:=none',
            f'robot_ip:={self._robot_ip}',
            f'username:={self._username}',
            f'password:={self._password}',
            f'rviz:={"true" if self._rviz else "false"}',
        ]
        self._log.info('No robot stack running; launching: '
                       + ' '.join(['ros2'] + [a for a in cmd[1:] if 'password' not in a]))
        try:
            # own process group
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                          start_new_session=True)
        except OSError as e:
            self._log.error(f'Failed to launch the robot stack: {e}')
            self._notify('Could not start the robot stack, check the kit terminal.')
            return None
        self._ready = False
        return self._proc

    def _watch(self, proc):
        """Report readiness and send a held instruction."""
        started = time.monotonic()
        warned = False
        while True:
            code = proc.poll()
            if code is not None:
                with self._lock:
                    expected = self._closing or self._user_stopped is proc
                    if self._proc is proc:
                        self._proc = None
                        self._ready = False
                    if not expected:
                        self._held = None
                if not expected:
                    self._log.error(f'Robot stack exited (code {code})')
                    self._notify(f'Robot stack stopped unexpectedly (code {code}). '
                                 'Send an instruction to restart it.')
                return

            if not self._ready and self._agent_listening():
                self._ready = True
                self._log.info('Robot stack ready')
                self._notify(ROBOT_READY)
                time.sleep(POLL_S)   # let the publisher match the new subscription
                self._flush_held()

            if (not self._ready and not warned
                    and time.monotonic() - started > self._ready_timeout):
                warned = True
                self._log.warn(f'Robot stack not ready after {self._ready_timeout:.0f}s')
                self._notify('The robot is taking longer than usual to start')

            time.sleep(POLL_S)

    def _wait_external_agent(self):
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self._closing:
                return
            if self._agent_listening():
                time.sleep(POLL_S)
                self._flush_held()
                return
            time.sleep(POLL_S)
        with self._lock:
            dropped = self._held is not None
            self._held = None
        if dropped:
            self._notify('The robot never became ready; your instruction was not sent.')

    def _flush_held(self):
        with self._lock:
            held, self._held = self._held, None
        if held is not None:
            text, publish = held
            self._log.info(f'Sending held instruction: {text}')
            publish(text)

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

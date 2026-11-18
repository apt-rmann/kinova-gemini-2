import asyncio
import json

from gemini_agent import skills as skill_registry
from gemini_agent.config import CONFIG_PATH
from gemini_agent.config_editor import ConfigEditor

class AppCommands:

    ROBOT_OPS = ('get_joint_angles',  'set_free_move')

    def __init__(self, cfg, session, emit):

        self.cfg = cfg
        self.session = session
        self.emit = emit
        self.config_editor = ConfigEditor(CONFIG_PATH, cfg, self._on_config_changed)
        self._tasks = set()

    def submit(self, raw: str):
        task = asyncio.ensure_future(self._handle(raw))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, raw: str):
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as e:
            reply = {'ok': False, 'message': f'invalid JSON: {e}'}
        else: 
            if isinstance(request, dict) and request.get('op') in self.ROBOT_OPS:
                reply = await self._robot_op(request)
            else:
                reply = self.config_editor.handle(request)
        self.emit('config', reply)

    async def _robot_op(self, req):
        robot = self.session.ctx.robot
        reply = {'op': req['op'], 'ok': True}
        if 'id' in req:
            reply['id'] = req['id']
        if req['op'] == 'get_joint_angles':
            angles = robot.joint_angles()
            if angles is None:
                reply.update(ok=False, message='no robot state yet')
            else:
                reply['joint_angles'] = angles
        else:
            enabled = bool(req.get('enabled'))
            res = await robot.set_free_move(enabled)
            reply['ok'] = res.ok
            if res.ok:
                reply['free_move'] = enabled
            else:
                reply['message'] = res.message
        reply.update(self.config_editor._snapshot())
        return reply

    def _on_config_changed(self):
        self.session.skills = skill_registry.load(self.cfg['skills'], self.cfg)
        if 'attach' in self.session.skills or 'detach' in self.session.skills or 'go_to_preset' in self.session.skills:
            self.session.request_reconfigure()
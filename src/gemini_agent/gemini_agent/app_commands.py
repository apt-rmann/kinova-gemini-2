import asyncio
import json

from gemini_agent import skills as skill_registry
from gemini_agent.config import CONFIG_PATH
from gemini_agent.config_editor import ConfigEditor

class AppCommands:

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
            reply = self.config_editor.handle(request)
        self.emit('config', reply)

    def _on_config_changed(self):
        self.session.skills = skill_registry.load(self.cfg['skills'], self.cfg)
        if 'attach' in self.session.skills or 'detach' in self.session.skills:
            self.session.request_reconfigure()
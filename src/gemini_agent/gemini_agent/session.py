"""Gemini Live session: send/receive loops, tool execution, interruption."""

import asyncio

from google.genai import types


class _Reconnect(Exception):
    """The server asked us to reconnect."""


class Session:
    def __init__(self, cfg, tools, ctx):
        self.cfg = cfg
        self.tools = tools
        self.ctx = ctx
        self.client = ctx.genai_client
        self.emit = ctx.emit

        with open(cfg['model']['system_prompt']) as f:
            self.system_prompt = f.read()

        self.instructions = asyncio.Queue()
        self.tool_task = None
        self.send_lock = asyncio.Lock()   # keeps image/text pairs from interleaving
        self.resume_handle = None
        self.interrupt_reason = ''
        self.s = None
        self._image_n = 0

    # --- configuration ---

    def live_config(self) -> types.LiveConnectConfig:
        model = self.cfg['model']
        config = types.LiveConnectConfig(
            response_modalities=['TEXT'],
            system_instruction=self.system_prompt,
            tools=[types.Tool(function_declarations=[t['declaration'] for t in self.tools.values()])],
            session_resumption=types.SessionResumptionConfig(handle=self.resume_handle),
        )

        level = str(model.get('thinking_level', 'none')).lower()
        if level != 'none':
            config.thinking_config = types.ThinkingConfig(
                thinking_level=level.upper(),
                include_thoughts=bool(model.get('include_thoughts')))

        if model.get('context_compression'):
            config.context_window_compression = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow())

        return config

    # --- main loop ---

    async def run(self):
        while True:
            try:
                async with self.client.aio.live.connect(
                        model=self.cfg['model']['name'], config=self.live_config()) as s:
                    self.s = s
                    self.emit('session', {'status': 'connected'})
                    await asyncio.gather(self.instruction_loop(), self.receive_loop())
            except asyncio.CancelledError:
                raise
            except _Reconnect:
                await self.cancel_tool('session lost')
            except Exception as e:
                self.emit('error', {'where': 'session', 'message': str(e)})
                await self.cancel_tool('session lost')
                await asyncio.sleep(2)

    async def instruction_loop(self):
        while True:
            text = await self.instructions.get()

            # the interrupted tool's response must be sent before the new user turn,
            # because the model has an open function call
            await self.cancel_tool('user sent a new message during motion')

            snapshot = await self.ctx.camera.fresh_snapshot(after=self.ctx.camera.now())
            async with self.send_lock:
                await self.send_image(snapshot.rgb, reason='user_turn')
                await self.s.send_client_content(
                    turns=types.Content(role='user', parts=[types.Part(text=self._user_text(text))]),
                    turn_complete=True)
            self.emit('user_text', {'text': text})

    def _user_text(self, text: str) -> str:
        state = self.ctx.robot.state()
        if 'ee_xyz' in state:
            x, y, z = state['ee_xyz']
            robot_line = f'ROBOT: ee=({x:.3f}, {y:.3f}, {z:.3f}) gripper={state["gripper"]:.2f}'
        else:
            robot_line = 'ROBOT: unknown'
        return f'USER: {text}\n{robot_line}'

    async def receive_loop(self):
        # receive() is a per-turn generator: it ends at turn_complete, so it has to be
        # re-entered for the next turn or nothing reads the socket again
        while True:
            async for msg in self.s.receive():
                await self._handle(msg)

    async def _handle(self, msg):
        if msg.tool_call:
            calls = msg.tool_call.function_calls or []
            if self.tool_task and not self.tool_task.done():
                # shouldn't happen with blocking tools
                await self.s.send_tool_response(function_responses=[
                    _resp(fc, {'ok': False, 'status': 'rejected',
                               'message': 'another action is running'}) for fc in calls])
            else:
                self.tool_task = asyncio.create_task(self.run_tools(calls))

        if msg.tool_call_cancellation:
            await self.cancel_tool('cancelled by model/server')

        if msg.server_content:
            content = msg.server_content
            if content.model_turn:
                for part in content.model_turn.parts or []:
                    if part.text:
                        self.emit('model_thought' if part.thought else 'model_text',
                                  {'text': part.text})
            if content.output_transcription and content.output_transcription.text:
                # this model streams its text here, not as model_turn parts
                self.emit('model_text', {'text': content.output_transcription.text})
            if content.turn_complete:
                self.emit('turn_complete', {})
            if content.interrupted:
                self.emit('interrupted', {})

        if msg.session_resumption_update and msg.session_resumption_update.new_handle:
            self.resume_handle = msg.session_resumption_update.new_handle

        if msg.go_away:
            self.emit('session', {'status': 'go_away',
                                  'time_left': str(msg.go_away.time_left)})
            raise _Reconnect()

    # --- tools ---

    async def run_tools(self, calls):
        responses, interrupted, image = [], False, None

        for fc in calls:
            if interrupted:
                responses.append(_resp(fc, {'ok': False, 'status': 'skipped',
                                            'reason': 'an earlier call was interrupted'}))
                continue

            args = dict(fc.args or {})
            self.emit('tool_call', {'id': fc.id, 'name': fc.name, 'args': args})
            try:
                result = await self.tools[fc.name]['fn'](self.ctx, **args)
            except asyncio.CancelledError:
                interrupted = True
                result = self.ctx.robot.interrupted_result(self.interrupt_reason)
            except TypeError as e:
                result = {'ok': False, 'status': 'error', 'message': f'bad arguments: {e}',
                          'robot': self.ctx.robot.state()}
            except Exception as e:
                result = {'ok': False, 'status': 'error', 'message': str(e),
                          'robot': self.ctx.robot.state()}

            image = result.pop('_image', image)
            self.emit('tool_result', {'id': fc.id, 'name': fc.name, 'result': result})
            responses.append(_resp(fc, result))

        async with self.send_lock:
            if not interrupted:  # interrupted: the user turn sends the frame
                snapshot = await self.ctx.camera.fresh_snapshot()
                rgb = image if image is not None else snapshot.rgb
                await self.send_image(
                    rgb, reason='look_annotated' if image is not None else 'post_tool')
            await self.s.send_tool_response(function_responses=responses)

    async def cancel_tool(self, reason: str):
        if self.tool_task and not self.tool_task.done():
            self.interrupt_reason = reason
            self.tool_task.cancel()
            # its tool response is sent before we continue
            await asyncio.gather(self.tool_task, return_exceptions=True)

    def on_stop(self):
        """From /stop. The C++ node has already halted the arm."""
        self.emit('session', {'status': 'stop requested'})
        asyncio.create_task(self.cancel_tool('stop requested'))

    # --- images ---

    async def send_image(self, rgb, reason: str):
        """The only place images go to the model, so the UI shows exactly what it saw."""
        jpeg = self.ctx.camera.encode_jpeg(rgb)
        self._image_n += 1
        image_id = f'img-{self._image_n:04d}'
        await self.s.send_realtime_input(video=types.Blob(data=jpeg, mime_type='image/jpeg'))
        self.ctx.publish_image(jpeg, image_id, reason)


def _resp(fc, result: dict) -> types.FunctionResponse:
    clean = {k: v for k, v in result.items() if not k.startswith('_')}
    return types.FunctionResponse(id=fc.id, name=fc.name, response=clean)

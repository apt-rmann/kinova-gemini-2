import os
import tempfile
import io
import re

from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken
from ruamel.yaml.comments import CommentedSeq

class ConfigError(Exception):
    """Config update request was refused"""

def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequenc=4, offset=2)
    y.width = 4096
    return y

def write_block(config_path: Path, block_name: str, entries: dict):
    real = Path(os.path.realpath(config_path))
    y = _yaml()
    with open(real, newline='') as f:
        original = f.read()
    doc = y.load(original.replace('\r\n', '\n'))

    block = doc.get(block_name)
    if block is None: 
        raise ConfigError(f'{real} has no holster block')

    for key in [k for k in block if k not in entries]:
        _delete_key(block, key)

    for key, value in entries.items():
        if key not in block:
            _append_key(block, key, value)
        elif isinstance(value, list):
            if list(block[key]) != value:
                block[key].clear()
                block[key].extend(value)
            elif block[key] != value:
                block[key] = value

    fd, tmp = tempfile.mkstemp(prefix='.config.', suffix='.yaml.tmp', dir=real.parent)
    try:
        buf = io.StringIO()
        y.dump(doc, buf)
        text = buf.getvalue()
        if not original.endswith(('\n', '\r')):
            text = text.rstrip('\n')
        if '\r\n' in original:
            text = text.replace('\n', '\r\n')
        with os.fdopen(fd, 'w', newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, os.stat(real).st_mode & 0o7777)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    
def _split_tail(block, key):
    """get comments for the key"""
    ca = block.ca.items.get(key)
    tok = ca[2] if ca else None
    if tok is None:
        return '', ''
    eol, _, rest = tok.value.partition('\n')
    return eol + '\n', rest

def _set_tail(block, key, eol, rest):
    ca = block.ca.items.get(key)
    tok = ca[2] if ca else None
    value = (eol or '\n') + rest
    if tok is not None:
        tok.value = value
    elif value != '\n':
        block.ca.items[key] = [None, None, CommentToken(value, CommentMark(0), None), None]

def _delete_key(block, name):
    """Delete a key, keep any comments after it"""
    keys = list(block)
    idx = keys.index(name)
    _, rest = _split_tail(block, name)
    del block[name]
    if rest and idx > 0:
        prev = keys[idx - 1]
        eol, prev_rest = _split_tail(block, prev)
        _set_tail(block, prev, eol, prev_rest + rest)

def _append_key(block, key, value):
    keys = list(block)
    if isinstance(value, list):
        value = CommentedSeq(value)
        value.fa.set_flow_style()
    block[key] = value

    if keys:
        last = keys[-1]
        eol, rest = _split_tail(block, last)
        if rest:
            _set_tail(block, last, eol, '')
            _set_tail(block, key, '', rest)

def validate_marker_id(marker_id) -> int:
    if not isinstance(marker_id, int) or not marker_id > 0:
        raise ConfigError(f'marker_id must be a whole number > 0')
    return marker_id

def validate_tool_name(tool) -> str:
    name_match = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$') #must start with letter, can contain digits or underscores, up to 32 chars long
    if not isinstance(tool, str) or not name_match.match(tool):
        raise ConfigError(f'tool name must start with a letter and must contain only letters, numbers, or underscores')
    return tool

class ConfigEditor:

    def __init__(self, config_path: Path, cfg: dict, on_config_changed):
        self.config_path = config_path
        self.cfg = cfg
        self.on_config_changed = on_config_changed

    def handle(self, request: dict) -> dict:
        reply = {'op': request.get('op') if isinstance(request, dict) else None}
        if isinstance(request, dict) and 'id' in request:
            reply['id'] = request['id']
        try:
            if not isinstance(request, dict):
                raise ConfigError('request must be a JSON')
            reply.update(self._dispatch(request))
            reply['ok'] = True
        except ConfigError as e:
            reply.update(ok=False, message=str(e))
        reply['holster'] = dict(self.cfg.get('holster')) or {}
        return reply

    def _dispatch(self, req: dict) -> dict:
        op = req.get('op')
        holster = self.cfg.get('holster') or {}

        if op == 'get_holster':
            return {}

        if op == 'set_holster_slot':
            marker_id = validate_marker_id(req.get('marker_id'))
            tool = validate_tool_name(req.get('tool'))
            for other_id, other in holster.items():
                if other_id != marker_id and str(other).lower() == tool.lower():
                    raise ConfigError(f"'{tool}' is already in the slot with marker {other_id}")
            new = dict(holster)
            created = marker_id not in new
            new[marker_id] = tool
            self._commit(new)
            return {'marker_id': marker_id, 'tool': tool, 'message': f"{'added' if created else 'updated'} marker {marker_id}: '{tool}"}

        if op == 'clear_holster_slot':
            marker_id = validate_marker_id(req.get('marker_id'))
            if marker_id not in holster:
                raise ConfigError(f'no holster slot with marker {marker_id}')
            new = {k: v for k, v in holster.items() if k != marker_id}
            self._commit(new)
            return {'marker_id': marker_id, 'message': f'cleared the slot with marker {marker_id}'}

        raise ConfigError(f"unkown operation {op!r}; expected get_holster, set_holster_slot, clear_holster_slot")

    def _commit(self, new_holster: dict):
        write_block(self.config_path, 'holster', new_holster)
        holster = self.cfg.setdefault('holster', {})
        holster.clear()
        holster.update(new_holster)
        self.on_config_changed()
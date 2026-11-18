import os
import tempfile
import io
import re
import math

from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken
from ruamel.yaml.comments import CommentedMap, CommentedSeq

HOLSTER_BLOCK = 'tools'   # config.yaml block: tool name -> {marker: id, length: m}
PRESET_BLOCK = 'presets'
PROTECTED_PRESETS = frozenset({'home', 'holster'})

MAX_TOOL_LENGTH = 1.0   # meters
JOINT_COUNT     = 7
MAX_JOINT_ANGLE = 360

class ConfigError(Exception):
    """Config update request was refused"""

def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
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
        raise ConfigError(f'{real} has no {block_name} block')

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
            block[key] = _flow(value)

    if len(block) == 0:
        block.fa.set_flow_style()   # empty block must stay `holster: {}` on its key's line

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

def _flow(value):
    """Lists and dicts are written inline, e.g. `fork: {marker: 2, length: 0.1}`."""
    if isinstance(value, list):
        value = CommentedSeq(value)
        value.fa.set_flow_style()
    elif isinstance(value, dict):
        value = CommentedMap(value)
        value.fa.set_flow_style()
    return value

def _append_key(block, key, value):
    keys = list(block)
    block[key] = _flow(value)

    if keys:
        last = keys[-1]
        eol, rest = _split_tail(block, last)
        if rest:
            _set_tail(block, last, eol, '')
            _set_tail(block, key, '', rest)

def validate_marker_id(marker_id) -> int:
    if isinstance(marker_id, bool) or not isinstance(marker_id, int) or not marker_id > 0:
        raise ConfigError(f'marker_id must be a whole number > 0')
    return marker_id

def validate_name(tool) -> str:
    name_match = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$') #must start with letter, can contain digits or underscores, up to 32 chars long
    if not isinstance(tool, str) or not name_match.match(tool):
        raise ConfigError(f'name must start with a letter and must contain only letters, numbers, or underscores')
    return tool

def validate_angles(angles) -> list:
    if not isinstance(angles, (list, tuple)) or len(angles) != JOINT_COUNT:
        raise ConfigError(f'angles must be a list of {JOINT_COUNT} numbers')
    out = []
    for i, a in enumerate(angles, start = 1):
        if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a):
            raise ConfigError(f'joint {i} angle must be a number of degrees')
        if not -MAX_JOINT_ANGLE <= a <= MAX_JOINT_ANGLE:
            raise ConfigError(f'joint {i} angle must be from -{MAX_JOINT_ANGLE:g} to {MAX_JOINT_ANGLE:g} degrees')
        out.append(float(a))

    return out

def validate_length(length) -> float:
    """Tool length in meters along tool +Z; missing means 0 (bare gripper TCP)."""
    if length is None:
        return 0.0
    if isinstance(length, bool) or not isinstance(length, (int, float)) \
            or not 0.0 <= length <= MAX_TOOL_LENGTH:
        raise ConfigError(f'length must be a number of meters from 0 to {MAX_TOOL_LENGTH}')
    return float(length)

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
        reply.update(self._snapshot())
        return reply

    def _dispatch(self, req: dict) -> dict:
        op = req.get('op')
        tools = _holster_snapshot(self.cfg)

        if op == 'get_holster':
            return {}

        if op == 'set_holster_slot':
            # put `tool` (with `length`) in the slot with `marker_id`, replacing whatever is there
            marker_id = validate_marker_id(req.get('marker_id'))
            tool = validate_name(req.get('tool'))
            length = validate_length(req.get('length'))
            for other_name, entry in tools.items():
                if other_name.lower() == tool.lower() and entry.get('marker') != marker_id:
                    raise ConfigError(f"'{other_name}' is already in the slot with marker {entry.get('marker')}")
            new, replaced = {}, None
            for name, entry in tools.items():
                if entry.get('marker') == marker_id:
                    replaced = name
                    new[tool] = _entry(marker_id, length)   # keep the slot's position in the block
                else:
                    new[name] = entry
            if replaced is None:
                new[tool] = _entry(marker_id, length)
            self._commit(HOLSTER_BLOCK, new)
            if replaced is None:
                message = f"added marker {marker_id}: '{tool}'"
            elif replaced == tool:
                message = f"updated marker {marker_id}: '{tool}'"
            else:
                message = f"marker {marker_id}: '{replaced}' -> '{tool}'"
            return {'marker_id': marker_id, 'tool': tool, 'length': length, 'message': message}

        if op == 'clear_holster_slot':
            marker_id = validate_marker_id(req.get('marker_id'))
            new = {k: v for k, v in tools.items() if v.get('marker') != marker_id}
            if len(new) == len(tools):
                raise ConfigError(f'no holster slot with marker {marker_id}')
            self._commit(HOLSTER_BLOCK, new)
            return {'marker_id': marker_id, 'message': f'cleared the slot with marker {marker_id}'}

        if op == 'get_presets':
            return {}
        
        if op == 'set_preset':
            name = validate_name(req.get('name'))
            angles = validate_angles(req.get('angles'))
            new = _preset_snapshot(self.cfg)
            existed = name in new
            new[name] = angles
            self._commit(PRESET_BLOCK, new)
            return {'name': name, 'message': f"{'updated' if existed else 'added'} preset '{name}'"}
        
        if op == 'delete_preset':
            name = validate_name(req.get('name'))
            if name in PROTECTED_PRESETS:
                raise ConfigError(f"'{name}' is used by the code and can't be deleted")
            new = _preset_snapshot(self.cfg)
            if new.pop(name, None) is None:
                raise ConfigError(f"no preset named '{name}'")
            self._commit(PRESET_BLOCK, new)
            return {'name': name, 'message': f"deleted preset '{name}'"}

        raise ConfigError(f"unknown operation {op!r}; expected get_holster, set_holster_slot, clear_holster_slot, get_presets, set_preset, delete_preset")

    def _commit(self, block_name: str, new: dict):
        write_block(self.config_path, block_name, new)
        live = self.cfg.setdefault(block_name, {})
        live.clear()
        live.update(new)
        self.on_config_changed()

    def _snapshot(self) -> dict:
        return {
            HOLSTER_BLOCK: _holster_snapshot(self.cfg),
            PRESET_BLOCK: _preset_snapshot(self.cfg)
        }


def _entry(marker_id: int, length: float) -> dict:
    """A `tools:` entry; length is left out when 0, matching the hand-written style."""
    return {'marker': marker_id, 'length': length} if length else {'marker': marker_id}

def _preset_snapshot(cfg: dict) -> dict:
    """Copy of 'presets:' block as {name: [7 floats]}"""
    return {str(name): [float(a) for a in (angles or [])] for name, angles in (cfg.get(PRESET_BLOCK) or {}).items()}

def _holster_snapshot(cfg: dict) -> dict:
    """Copy of the `tools:` block as {name: {'marker': id, 'length': m}}."""
    return {str(name): dict(entry or {}) for name, entry in (cfg.get(HOLSTER_BLOCK) or {}).items()}

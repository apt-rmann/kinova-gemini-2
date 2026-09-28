"""Tool registry. Every tool is one decorated async function fn(ctx, **args) -> dict."""

from dataclasses import dataclass
from typing import Any, Callable

REGISTRY: dict = {}


@dataclass
class ToolContext:
    robot: Any
    camera: Any
    cfg: dict
    emit: Callable
    publish_image: Callable
    genai_client: Any


def tool(name: str, description: str, parameters: dict):
    """Register an async tool. parameters is an OpenAPI-style JSON schema."""
    def wrap(fn):
        REGISTRY[name] = {
            'fn': fn,
            'declaration': {'name': name, 'description': description, 'parameters': parameters},
        }
        return fn
    return wrap


from . import motion, perception  # noqa: E402,F401  (registers tools)


def load(names: list, cfg: dict) -> dict:
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise ValueError(f'unknown tools in config.yaml: {missing}')
    tools = {n: REGISTRY[n] for n in names}
    if 'go_to_preset' in tools:  # fill the preset enum from config
        tools['go_to_preset']['declaration']['parameters']['properties']['name']['enum'] = list(cfg['presets'])
    return tools

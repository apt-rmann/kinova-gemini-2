"""Skill registry. Every skill is one decorated async function fn(ctx, **args) -> dict that
composes robot primitives (the controller's ROS 2 actions) into one parameterized ability."""

from dataclasses import dataclass
from typing import Any, Callable

REGISTRY: dict = {}


@dataclass
class SkillContext:
    robot: Any
    camera: Any
    cfg: dict
    emit: Callable
    publish_image: Callable
    genai_client: Any


def skill(name: str, description: str, parameters: dict):
    """Register an async skill. parameters is an OpenAPI-style JSON schema."""
    def wrap(fn):
        REGISTRY[name] = {
            'fn': fn,
            'declaration': {'name': name, 'description': description, 'parameters': parameters},
        }
        return fn
    return wrap


from . import grasp, holster, itch, motion, perception  # noqa: E402,F401  (registers skills)


def load(names: list, cfg: dict) -> dict:
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise ValueError(f'unknown skills in config.yaml: {missing}')
    skills = {n: REGISTRY[n] for n in names}
    if 'go_to_preset' in skills:  # fill the preset enum from config
        skills['go_to_preset']['declaration']['parameters']['properties']['name']['enum'] = list(cfg['presets'])
    for name in ('attach', 'detach'):  # fill the holster enum from config
        if name in skills:
            skills[name]['declaration']['parameters']['properties']['name']['enum'] = \
                holster.holster_names(cfg)
    return skills

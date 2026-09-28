"""Loads config.yaml and .env once, for everything else in the package."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _config_path() -> Path:
    """$KINOVA_GEMINI_CONFIG, else the config.yaml at the root of this checkout."""
    env_path = os.environ.get('KINOVA_GEMINI_CONFIG')
    if env_path:
        return Path(env_path).expanduser()

    # walk up from this file so the repo works wherever it is checked out
    # (with --symlink-install this lands in the source tree)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'config.yaml'
        if candidate.is_file():
            return candidate

    return Path.home() / 'kinova-gemini' / 'config.yaml'


CONFIG_PATH = _config_path()
REPO_ROOT = CONFIG_PATH.parent


def load_config() -> dict:
    """Read config.yaml, load .env, and resolve model.system_prompt against the repo root."""
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f'config.yaml not found at {CONFIG_PATH}. '
            'Set KINOVA_GEMINI_CONFIG to its path.')

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    load_dotenv(REPO_ROOT / '.env')
    if not os.environ.get('GEMINI_API_KEY'):
        raise RuntimeError(
            f'GEMINI_API_KEY is not set. Copy {REPO_ROOT}/.env.example to '
            f'{REPO_ROOT}/.env and put your key in it.')

    prompt = cfg.get('model', {}).get('system_prompt')
    if prompt:
        cfg['model']['system_prompt'] = str(REPO_ROOT / prompt)

    return cfg

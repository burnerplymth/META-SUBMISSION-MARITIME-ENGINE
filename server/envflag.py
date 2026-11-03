"""One parser for on/off environment switches.

The kill switches (SUPERMAP_LLM_DISABLED, SUPERMAP_VOICE_DISABLED,
SATWATCH_SAM_DISABLED, SUPERMAP_AGENT_DISABLED, SUPERMAP_BRIEF_DISABLED) all
read 1 / true / yes / on, any case, surrounding space ignored, so one value
stops every paid path.
"""
from __future__ import annotations

import os

TRUTHY = frozenset({"1", "true", "yes", "on"})


def truthy(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in TRUTHY


def env_truthy(name: str, env=None) -> bool:
    return truthy((os.environ if env is None else env).get(name))


def any_truthy(*names: str, env=None) -> bool:
    return any(env_truthy(n, env) for n in names)

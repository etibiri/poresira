# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed readers for environment variables."""

import os

TRUE_VALUES = {"1", "true", "yes", "on"}


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in TRUE_VALUES


def env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    return int(raw_value) if raw_value else default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env_str(name, default).split(",") if item.strip()]

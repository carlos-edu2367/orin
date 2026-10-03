"""Which deployment shape this process runs as.

``local`` is the personal install (loopback trust, one fixed principal).
``server`` is a network-facing instance shared by named user profiles.
"""
from __future__ import annotations

import os
from enum import StrEnum
from typing import Mapping


class RuntimeMode(StrEnum):
    LOCAL = "local"
    SERVER = "server"


def current_mode(environ: Mapping[str, str] | None = None) -> RuntimeMode:
    """The mode from ``ORIN_MODE``; anything unknown is a configuration error, never a silent default."""
    raw = (environ if environ is not None else os.environ).get("ORIN_MODE", "").strip().lower()
    if not raw:
        return RuntimeMode.LOCAL
    return RuntimeMode(raw)


__all__ = ["RuntimeMode", "current_mode"]

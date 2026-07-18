"""
Legacy re-export — LocalSocketStore moved to local_daemon.py as LocalStore.
"""

from __future__ import annotations

from .local_daemon import LocalStore as LocalSocketStore

__all__ = ["LocalSocketStore"]

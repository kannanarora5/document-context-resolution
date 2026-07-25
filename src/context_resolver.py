"""Backward-compatible alias for `resolver` (Day 3 entrypoint)."""

from resolver import *  # noqa: F403
from resolver import enrich_chunks_file, main  # noqa: F401

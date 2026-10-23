from __future__ import annotations

from . import schemas
from .context import ToolContext


def memory_get(args: schemas.MemoryGetArgs, context: ToolContext) -> dict:
    value = context.history.memory_get(args.key)
    return {"key": args.key, "value": value, "found": value is not None}


def memory_set(args: schemas.MemorySetArgs, context: ToolContext) -> dict:
    context.history.memory_set(args.key, args.value)
    return {"key": args.key, "stored": True}

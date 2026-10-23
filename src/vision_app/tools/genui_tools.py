from __future__ import annotations

from pydantic import BaseModel


def payload(kind: str, args: BaseModel) -> dict:
    data = args.model_dump(mode="json")
    return {"_genui": {"kind": kind, **data}, "rendered": kind, "title": data.get("title") or data.get("filename", "")}


def ui_table(args) -> dict:
    return payload("table", args)


def ui_chart(args) -> dict:
    return payload("chart", args)


def ui_cards(args) -> dict:
    return payload("cards", args)


def ui_timeline(args) -> dict:
    return payload("timeline", args)


def ui_code_diff(args) -> dict:
    return payload("diff", args)

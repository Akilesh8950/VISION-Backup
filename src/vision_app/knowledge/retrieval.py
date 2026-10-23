from __future__ import annotations

from .index import KnowledgeIndex


def build_project_context(index: KnowledgeIndex, project_id: str, prompt: str, limit: int = 5) -> tuple[str, list[dict]]:
    rows = index.search(project_id, prompt, limit=limit)
    if not rows:
        return "", []
    sources: list[dict] = []
    blocks = ["Relevant local project sources:"]
    for position, row in enumerate(rows, start=1):
        source_id = f"L{position}"
        source = {
            "source_id": source_id,
            "title": row["display_name"],
            "url": "",
            "path": row["source_path"],
            "locator": row["locator"],
            "snippet": row["text"][:500],
        }
        sources.append(source)
        blocks.append(f"[{source_id}] {row['display_name']} ({row['locator']})\n{row['text']}")
    blocks.append("Cite local document claims with these [L#] IDs. Do not cite a source that is not listed.")
    return "\n\n".join(blocks), sources

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
    ".ini", ".cfg", ".csv", ".html", ".css", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs",
    ".sql", ".xml", ".log",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


@dataclass(frozen=True)
class ParsedSection:
    text: str
    locator: str


def is_secret_like(path: Path) -> bool:
    name = path.name.lower()
    return name in {".env"} or name.startswith(".env.") or "secret" in name or "credential" in name or name.endswith((".pem", ".key", ".pfx"))


def parse_file(path: Path, max_bytes: int = 10_000_000) -> list[ParsedSection]:
    if is_secret_like(path):
        raise ValueError("Secret-like files are excluded from knowledge indexing")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("File exceeds the 10 MB knowledge indexing limit")
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        text = path.read_text(encoding="utf-8", errors="strict")
        lines = text.splitlines()
        sections: list[ParsedSection] = []
        step = 80
        for start in range(0, len(lines), step):
            chunk = "\n".join(lines[start:start + step]).strip()
            if chunk:
                sections.append(ParsedSection(chunk, f"lines {start + 1}-{min(len(lines), start + step)}"))
        return sections or [ParsedSection("", "lines 1-0")]
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise RuntimeError("PDF support requires the pypdf package") from exc
        reader = PdfReader(str(path))
        return [ParsedSection((page.extract_text() or "").strip(), f"page {index + 1}") for index, page in enumerate(reader.pages)]
    if suffix == ".docx":
        try:
            import docx  # type: ignore
        except Exception as exc:
            raise RuntimeError("DOCX support requires the python-docx package") from exc
        document = docx.Document(str(path))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        sections: list[ParsedSection] = []
        step = 40
        for start in range(0, len(paragraphs), step):
            chunk = "\n".join(paragraphs[start:start + step]).strip()
            if chunk:
                sections.append(ParsedSection(chunk, f"paragraphs {start + 1}-{min(len(paragraphs), start + step)}"))
        return sections
    raise ValueError(f"Unsupported knowledge file type: {suffix or '(none)'}")

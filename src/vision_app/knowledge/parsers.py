from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
    ".ini", ".cfg", ".csv", ".html", ".css", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs",
    ".sql", ".xml", ".log",
}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}
MAX_EXTRACTED_CHARS = 5_000_000
MAX_PDF_PAGES = 1_000
MAX_DOCX_PARAGRAPHS = 50_000
MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class ParsedSection:
    text: str
    locator: str


def is_secret_like(path: Path) -> bool:
    name = path.name.lower()
    return name in {".env"} or name.startswith(".env.") or "secret" in name or "credential" in name or name.endswith((".pem", ".key", ".pfx"))


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        # A large share of legacy Windows text files are CP1252. Falling back
        # only after strict UTF-8 keeps normal files exact while making those
        # documents indexable instead of failing the whole source.
        return data.decode("cp1252", errors="replace")


def _check_extracted_size(sections: list[ParsedSection]) -> list[ParsedSection]:
    total = sum(len(section.text) for section in sections)
    if total > MAX_EXTRACTED_CHARS:
        raise ValueError("Extracted document text exceeds the 5 million character knowledge limit")
    return sections


def parse_file(path: Path, max_bytes: int = 10_000_000) -> list[ParsedSection]:
    if is_secret_like(path):
        raise ValueError("Secret-like files are excluded from knowledge indexing")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("File exceeds the 10 MB knowledge indexing limit")
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("File exceeds the 10 MB knowledge indexing limit")
        text = _decode_text(data)
        lines = text.splitlines()
        text_sections: list[ParsedSection] = []
        step = 80
        for start in range(0, len(lines), step):
            chunk = "\n".join(lines[start:start + step]).strip()
            if chunk:
                text_sections.append(ParsedSection(chunk, f"lines {start + 1}-{min(len(lines), start + step)}"))
        return _check_extracted_size(text_sections or [ParsedSection("", "lines 1-0")])
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise RuntimeError("PDF support requires the pypdf package") from exc
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError("PDF exceeds the 1000 page knowledge indexing limit")
            pdf_sections: list[ParsedSection] = []
            extracted_chars = 0
            for index, page in enumerate(reader.pages):
                page_text = (page.extract_text() or "").strip()
                extracted_chars += len(page_text)
                if extracted_chars > MAX_EXTRACTED_CHARS:
                    raise ValueError("Extracted document text exceeds the 5 million character knowledge limit")
                pdf_sections.append(ParsedSection(page_text, f"page {index + 1}"))
        return pdf_sections
    if suffix == ".docx":
        try:
            import docx  # type: ignore
        except Exception as exc:
            raise RuntimeError("DOCX support requires the python-docx package") from exc
        try:
            with zipfile.ZipFile(path) as archive:
                expanded_bytes = sum(max(0, info.file_size) for info in archive.infolist())
        except zipfile.BadZipFile as exc:
            raise ValueError("DOCX file is not a valid Office package") from exc
        if expanded_bytes > MAX_DOCX_EXPANDED_BYTES:
            raise ValueError("DOCX expanded content exceeds the 50 MB knowledge indexing limit")
        with path.open("rb") as stream:
            document = docx.Document(stream)
            paragraphs: list[str] = []
            extracted_chars = 0
            for paragraph in document.paragraphs:
                text = paragraph.text
                extracted_chars += len(text)
                if extracted_chars > MAX_EXTRACTED_CHARS:
                    raise ValueError("Extracted document text exceeds the 5 million character knowledge limit")
                paragraphs.append(text)
                if len(paragraphs) > MAX_DOCX_PARAGRAPHS:
                    raise ValueError("DOCX exceeds the 50000 paragraph knowledge indexing limit")
        docx_sections: list[ParsedSection] = []
        step = 40
        for start in range(0, len(paragraphs), step):
            chunk = "\n".join(paragraphs[start:start + step]).strip()
            if chunk:
                docx_sections.append(ParsedSection(chunk, f"paragraphs {start + 1}-{min(len(paragraphs), start + step)}"))
        return _check_extracted_size(docx_sections)
    raise ValueError(f"Unsupported knowledge file type: {suffix or '(none)'}")

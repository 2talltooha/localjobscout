from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import spacy
from spacy.language import Language


@lru_cache(maxsize=1)
def get_nlp() -> Language:
    try:
        model: Language = spacy.load("en_core_web_sm")
        return model
    except OSError as exc:
        raise RuntimeError(
            "spaCy model 'en_core_web_sm' is not installed. "
            "Run: python -m spacy download en_core_web_sm"
        ) from exc


def _load_pdf(path: Path) -> str:
    try:
        import pypdf
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is not installed; run: pip install pypdf"
        ) from exc
    reader = pypdf.PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages)
    if not text.strip():
        raise ValueError(
            f"Could not extract text from PDF at {path}. "
            "The PDF may be image-only (scanned). "
            "Convert to a text-selectable PDF or use a .txt resume instead."
        )
    return text


def load_resume(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found at {path}. "
            "Drop a plain-text (.txt) or PDF (.pdf) resume there and try again."
        )
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    # Default: plain text (utf-8-sig strips BOM automatically if present)
    return path.read_text(encoding="utf-8-sig")


def preprocess(text: str, nlp: Language) -> str:
    doc = nlp(text.lower())

    tokens: list[str] = [
        token.lemma_
        for token in doc
        if not token.is_stop
        and not token.is_punct
        and not token.is_space
        and token.lemma_.strip()
    ]

    chunk_tokens: list[str] = []
    for chunk in doc.noun_chunks:
        parts = [
            token.lemma_
            for token in chunk
            if not token.is_stop
            and not token.is_punct
            and not token.is_space
            and token.lemma_.strip()
        ]
        if parts:
            chunk_tokens.append("_".join(parts))

    return " ".join(tokens + chunk_tokens)

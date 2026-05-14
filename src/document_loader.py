"""
Document loader for Agent 1.
Scans data/documents/{application_id}/ recursively and returns typed DocumentInput objects.
Supports both flat layouts (legacy) and channel-subdirectory layouts (email/, scanned/, pdf/).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from models.extraction import DocumentInput

logger = logging.getLogger(__name__)

_DOCS_ROOT = Path(os.path.dirname(os.path.dirname(__file__))) / "data" / "documents"

# All file extensions this loader will process.
# Add new extensions here as new extractor functions are implemented below.
_SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".eml"}

# Stem-based type inference (applied to filename stem)
_STEM_TYPE_MAP: dict[str, str] = {
    "email_submission": "email",
    "rm_submission_email": "email",
    "submission_email": "email",
    "application_form": "application_form",
    "signed_application_form": "application_form",
    "onboarding_form": "application_form",
    "supporting_docs": "supporting_document",
    "additional_info": "additional_info",
    "amendment": "additional_info",
    "clarification": "additional_info",
}

# Subdirectory-based type override (parent dir name → document type)
_DIR_TYPE_MAP: dict[str, str] = {
    "email": "email",
    "scanned": "application_form",
    "pdf": "supporting_document",
    "docs": "supporting_document",
    "attachments": "supporting_document",
}


def _infer_type(path: Path, root: Path) -> str:
    """Infer document type from parent directory name first, then filename stem."""
    parent_name = path.parent.name.lower()
    if parent_name in _DIR_TYPE_MAP and path.parent != root:
        return _DIR_TYPE_MAP[parent_name]
    stem = path.stem.lower()
    for key, doc_type in _STEM_TYPE_MAP.items():
        if key in stem:
            return doc_type
    return "supporting_document"


# ── Per-format extractors ──────────────────────────────────────────────────────
# Each function receives a Path and returns the document text that Stage 0 will see.
# Swap the stub bodies for real library calls when the dependency is available.

def _extract_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_pdf(path: Path) -> str:
    # TODO: replace with pymupdf (fitz) or pdfplumber for native-text PDFs;
    # fall back to pytesseract for scanned/image-only PDFs.
    raise NotImplementedError(f"PDF extraction not yet wired up for {path.name}")


def _extract_image(path: Path) -> str:
    # TODO: call pytesseract.image_to_string(PIL.Image.open(path))
    raise NotImplementedError(f"Image OCR not yet wired up for {path.name}")


def _extract_eml(path: Path) -> str:
    # TODO: use Python's email.message_from_bytes to parse headers + body,
    # strip HTML if needed, and concatenate any text/* attachments.
    raise NotImplementedError(f"Email (.eml) extraction not yet wired up for {path.name}")


def _extract_content(path: Path) -> str:
    """Dispatch to the right extractor based on file extension."""
    ext = path.suffix.lower()
    if ext == ".txt":
        return _extract_txt(path)
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in {".png", ".jpg", ".jpeg", ".tiff"}:
        return _extract_image(path)
    if ext == ".eml":
        return _extract_eml(path)
    raise ValueError(f"No extractor registered for extension '{ext}'")


# ── Public loader ──────────────────────────────────────────────────────────────

def load_documents(application_id: str) -> list[DocumentInput]:
    """
    Load all supported documents for a given application_id.
    Walks the directory recursively so both flat and subdirectory layouts are handled.
    Returns an empty list (with a warning) if the directory does not exist.
    """
    doc_dir = _DOCS_ROOT / application_id
    if not doc_dir.exists():
        logger.warning(
            "[DocumentLoader] No document directory found for %s at %s",
            application_id,
            doc_dir,
        )
        return []

    documents: list[DocumentInput] = []
    for path in sorted(p for p in doc_dir.rglob("*") if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS):
        try:
            content = _extract_content(path)
            rel_name = str(path.relative_to(doc_dir))
            doc_type = _infer_type(path, doc_dir)
            documents.append(DocumentInput(
                filename=rel_name,
                document_type=doc_type,
                content=content,
                file_path=str(path),
            ))
            logger.debug("[DocumentLoader] Loaded %s as %s", rel_name, doc_type)
        except NotImplementedError as exc:
            logger.warning("[DocumentLoader] Skipping %s — %s", path.name, exc)
        except OSError as exc:
            logger.error("[DocumentLoader] Failed to read %s: %s", path, exc)

    logger.info("[DocumentLoader] Loaded %d documents for %s", len(documents), application_id)
    return documents

# TODOs

## PDF Extraction for application_form.pdf

**Trigger:** `[DocumentLoader] Skipping application_form.pdf — PDF extraction not yet wired up`

**Files to change:**
- `pyproject.toml` — add `pymupdf>=1.24.0` to dependencies
- `src/document_loader.py:69` — replace `_extract_pdf` stub
- `tests/test_data_loader.py` — add 3 test cases (text PDF, empty PDF, missing file)

**Implementation:**

1. `uv add pymupdf`

2. Replace stub in `src/document_loader.py`:
   ```python
   def _extract_pdf(path: Path) -> str:
       import fitz  # pymupdf
       doc = fitz.open(str(path))
       pages = [page.get_text() for page in doc]
       doc.close()
       text = "\n\n".join(p.strip() for p in pages if p.strip())
       if not text:
           logger.warning(
               "[DocumentLoader] %s yielded no text — may be scanned/image-only; consider OCR",
               path.name,
           )
       return text
   ```

3. Verify: `python -c "from src.document_loader import load_documents; print(load_documents('APP-2026-0301'))"`

**Notes:**
- PDFs confirmed text-based (have `/Font`), so no OCR needed now
- `import fitz` kept inside function so module loads without pymupdf installed
- `_extract_image` and `_extract_eml` stubs still pending — same pattern applies
- No changes needed to `run_phase1.py` or Stage 0

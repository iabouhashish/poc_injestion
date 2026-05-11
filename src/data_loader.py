"""
CSV ingestion module — reads crestview_client_applications.csv and maps each
row to a ClientApplication Pydantic model.

Note: the CSV header contains a typo: "submiss ion_date" (space in column name).
This is handled by stripping and normalising all column names on load.
"""
import csv
import logging
from pathlib import Path

from models.client import ClientApplication

logger = logging.getLogger(__name__)

# Map from normalised CSV column name → ClientApplication field name
_COLUMN_MAP = {
    "application_id": "application_id",
    "client_name": "client_name",
    "client_type": "client_type",
    "requested_services": "requested_services",
    "estimated_aum": "estimated_aum",
    "submission_date": "submission_date",
    "submissiondate": "submission_date",    # handles the "submiss ion_date" typo after strip
    "status": "status",
    "description": "description",
}


def _normalise_key(raw: str) -> str:
    """Strip whitespace, lowercase, remove spaces — makes the typo harmless."""
    return raw.strip().lower().replace(" ", "")


def _fix_csv_content(raw: str) -> str:
    """
    The source CSV has rows that share a line: a quoted field closes with `"`
    and the next APP-XXXX-XXXX id starts immediately after a space, without a
    newline. Insert the missing newline before each APP-XXXX id that appears
    mid-line (i.e. not at the start of a line).
    """
    import re
    # Insert \n before any APP-XXXX-XXXX that is NOT at the start of a line
    fixed = re.sub(r'(?<!\n)(APP-\d{4}-\d{4})', r'\n\1', raw)
    return fixed


def load_applications(csv_path: str | Path) -> list[ClientApplication]:
    """
    Read the Crestview client applications CSV and return a list of
    validated ClientApplication models.

    Raises FileNotFoundError if the CSV does not exist.
    Logs and skips rows that fail Pydantic validation.
    """
    import io

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    raw = csv_path.read_text(encoding="utf-8-sig")
    fixed = _fix_csv_content(raw)

    applications: list[ClientApplication] = []

    with io.StringIO(fixed) as fh:
        reader = csv.DictReader(fh)

        for row_num, raw_row in enumerate(reader, start=2):  # row 1 is header
            # Normalise column names
            row = {_normalise_key(k): v.strip() for k, v in raw_row.items() if k}

            # Build kwargs for the model using the column map
            kwargs: dict = {}
            for csv_col, model_field in _COLUMN_MAP.items():
                if csv_col in row and row[csv_col]:
                    kwargs[model_field] = row[csv_col]

            if not kwargs.get("application_id"):
                logger.warning("Row %d: missing application_id — skipping", row_num)
                continue

            try:
                app = ClientApplication(**kwargs)
                applications.append(app)
                logger.debug(
                    "Loaded application %s — %s",
                    app.application_id,
                    app.client_name,
                )
            except Exception as exc:
                logger.error(
                    "Row %d (application_id=%s): validation failed — %s",
                    row_num,
                    kwargs.get("application_id", "unknown"),
                    exc,
                )

    logger.info("Loaded %d client applications from %s", len(applications), csv_path)
    return applications

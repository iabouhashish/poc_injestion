"""Tests for the CSV data loader."""
from __future__ import annotations

import csv
import textwrap
from pathlib import Path

import pytest

from src.data_loader import load_applications, _fix_csv_content, _normalise_key


HEADER = "application_id,client_name,client_type,requested_services,estimated_aum,submission_date,status,description"


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(HEADER + "\n" + "\n".join(rows), encoding="utf-8")


# ── _normalise_key ─────────────────────────────────────────────────────────────

class TestNormaliseKey:
    def test_strips_whitespace(self):
        assert _normalise_key("  application_id  ") == "application_id"

    def test_lowercases(self):
        assert _normalise_key("ApplicationID") == "applicationid"

    def test_removes_spaces(self):
        # The CSV header has "submiss ion_date" — space is removed, underscore preserved
        # "submiss ion_date" → remove space → "submission_date"
        assert _normalise_key("submiss ion_date") == "submission_date"

    def test_handles_empty(self):
        assert _normalise_key("") == ""


# ── _fix_csv_content ───────────────────────────────────────────────────────────

class TestFixCsvContent:
    def test_inserts_newline_before_app_id_mid_line(self):
        raw = 'APP-2026-0302,...,"Some description." APP-2026-0303,...'
        fixed = _fix_csv_content(raw)
        assert "\nAPP-2026-0303" in fixed

    def test_does_not_insert_newline_between_already_separated_rows(self):
        # Both rows are on separate lines already — no extra blank lines should appear between them
        raw = "APP-2026-0301,...\nAPP-2026-0302,..."
        fixed = _fix_csv_content(raw)
        # The two rows must remain on separate lines without an extra blank line between them
        lines = [l for l in fixed.strip().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_handles_multiple_concatenated_rows(self):
        raw = 'X,...,"desc." APP-2026-0303,...,"desc2." APP-2026-0304,...'
        fixed = _fix_csv_content(raw)
        assert fixed.count("\nAPP-") == 2

    def test_no_change_when_no_app_ids(self):
        raw = "header,row\ndata,row"
        fixed = _fix_csv_content(raw)
        assert fixed == raw


# ── load_applications — real CSV ───────────────────────────────────────────────

class TestLoadApplicationsRealCsv:
    def test_loads_all_fifteen_applications(self):
        apps = load_applications("crestview_client_applications.csv")
        assert len(apps) == 15

    def test_all_application_ids_unique(self):
        apps = load_applications("crestview_client_applications.csv")
        ids = [a.application_id for a in apps]
        assert len(ids) == len(set(ids))

    def test_expected_ids_present(self):
        apps = load_applications("crestview_client_applications.csv")
        ids = {a.application_id for a in apps}
        for i in range(1, 16):
            assert f"APP-2026-0{300 + i}" in ids

    def test_aum_parsed_as_float(self):
        apps = load_applications("crestview_client_applications.csv")
        for app in apps:
            assert isinstance(app.estimated_aum, float)
            assert app.estimated_aum > 0

    def test_app_0303_loads_correctly(self):
        """APP-2026-0303 (Thornwood University Endowment) must load with correct data."""
        apps = load_applications("crestview_client_applications.csv")
        app = next((a for a in apps if a.application_id == "APP-2026-0303"), None)
        assert app is not None
        assert app.client_name == "Thornwood University Endowment Fund"
        assert app.estimated_aum == 280_000_000.0

    def test_app_0312_loads_correctly(self):
        """APP-2026-0312 (Laguna Capital) must load with correct data."""
        apps = load_applications("crestview_client_applications.csv")
        mw = next((a for a in apps if a.application_id == "APP-2026-0312"), None)
        assert mw is not None
        assert mw.estimated_aum == 85_000_000.0

    def test_descriptions_non_empty(self):
        apps = load_applications("crestview_client_applications.csv")
        for app in apps:
            assert len(app.description) > 20, f"{app.application_id} has a suspiciously short description"

    def test_app_0309_aum(self):
        apps = load_applications("crestview_client_applications.csv")
        app = next(a for a in apps if a.application_id == "APP-2026-0309")
        assert app.estimated_aum == 65_000_000.0


# ── load_applications — synthetic CSVs ────────────────────────────────────────

class TestLoadApplicationsSyntheticCsv:
    def test_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_applications(tmp_path / "does_not_exist.csv")

    def test_single_valid_row(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        _write_csv(csv_file, [
            'APP-TEST-0001,Test Corp,Corporate,Wealth Management,5000000,01/01/2026,New,"Test description."',
        ])
        apps = load_applications(csv_file)
        assert len(apps) == 1
        assert apps[0].application_id == "APP-TEST-0001"
        assert apps[0].estimated_aum == 5_000_000.0

    def test_skips_row_with_missing_application_id(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        _write_csv(csv_file, [
            'APP-TEST-0001,Good Corp,Corporate,X,1000000,01/01/2026,New,"Valid."',
            ',Bad Corp,Corporate,X,1000000,01/01/2026,New,"Missing ID."',
        ])
        apps = load_applications(csv_file)
        assert len(apps) == 1
        assert apps[0].application_id == "APP-TEST-0001"

    def test_handles_header_typo_submission_date(self, tmp_path):
        """The real CSV has 'submiss ion_date' — the loader must handle this."""
        typo_header = "application_id,client_name,client_type,requested_services,estimated_aum,submiss ion_date,status,description"
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(
            typo_header + "\n" +
            'APP-TEST-0001,X,Corporate,X,1000000,01/01/2026,New,"Desc."',
            encoding="utf-8",
        )
        apps = load_applications(csv_file)
        assert len(apps) == 1
        assert apps[0].submission_date == "01/01/2026"

    def test_joined_rows_fixed_automatically(self, tmp_path):
        """Two rows concatenated on the same line should both be loaded.
        The fix regex matches APP-YYYY-NNNN format (matching the real CSV ids).
        """
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(
            HEADER + "\n" +
            'APP-2026-9001,Corp A,Corporate,X,1000000,01/01/2026,New,"Desc A." '
            'APP-2026-9002,Corp B,Corporate,X,2000000,01/02/2026,New,"Desc B."',
            encoding="utf-8",
        )
        apps = load_applications(csv_file)
        assert len(apps) == 2
        ids = {a.application_id for a in apps}
        assert "APP-2026-9001" in ids
        assert "APP-2026-9002" in ids

    def test_aum_parsed_from_string_with_commas(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        _write_csv(csv_file, [
            'APP-TEST-0001,X,Corporate,X,"1,500,000",01/01/2026,New,"Desc."',
        ])
        apps = load_applications(csv_file)
        assert apps[0].estimated_aum == 1_500_000.0

    def test_multiple_valid_rows(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        rows = [
            f'APP-TEST-{i:04d},Client {i},Corporate,X,{i * 1_000_000},01/01/2026,New,"Desc {i}."'
            for i in range(1, 6)
        ]
        _write_csv(csv_file, rows)
        apps = load_applications(csv_file)
        assert len(apps) == 5

# Basic test suite for ProPrintPreflight. This is not meant to be exhaustive, but it should cover the basics of the preflight process.

from pathlib import Path
import re

import pro_print_preflight_agent as app


def test_detect_preset_from_filename_postcard_5x7():
    preset = app.detect_preset_from_filename("customer_postcard_5x7_final.pdf")

    assert preset["name"] == "postcard_5x7"
    assert preset["trim"] == (5.0, 7.0)
    assert preset["expected_pages"] == 2


def test_get_app_dir_uses_executable_parent_when_frozen(tmp_path, monkeypatch):
    fake_exe = tmp_path / "ProPrintPreflightAgent.exe"

    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "executable", str(fake_exe))

    assert app.get_app_dir() == tmp_path


def test_validate_pdf_file_rejects_non_pdf(tmp_path):
    text_file = tmp_path / "not-a-pdf.txt"
    text_file.write_text("hello", encoding="utf-8")

    result = app.validate_pdf_file(text_file)

    assert not result.is_valid
    assert result.reason == "Not a PDF file"


def test_validate_pdf_file_rejects_empty_pdf(tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"")

    result = app.validate_pdf_file(pdf)

    assert not result.is_valid
    assert result.reason == "Empty file"


def test_safe_move_file_avoids_overwriting_existing_file(tmp_path):
    incoming = tmp_path / "Incoming"
    passed = tmp_path / "Passed"
    incoming.mkdir()
    passed.mkdir()

    src = incoming / "job.pdf"
    existing = passed / "job.pdf"
    src.write_bytes(b"new pdf content")
    existing.write_bytes(b"existing pdf content")

    moved_to = app.safe_move_file(src, passed)

    assert moved_to == passed / "job_1.pdf"
    assert moved_to.read_bytes() == b"new pdf content"
    assert existing.read_bytes() == b"existing pdf content"
    assert not src.exists()


def test_wait_until_file_is_ready_returns_true_for_stable_file(tmp_path):
    pdf = tmp_path / "ready.pdf"
    pdf.write_bytes(b"%PDF-1.4 test content")

    assert app.wait_until_file_is_ready(
        pdf,
        stable_seconds=1,
        timeout_seconds=4,
    )


def test_ensure_directories_creates_expected_folders(tmp_path, monkeypatch):
    base_dir = tmp_path / "Preflight_System"

    monkeypatch.setattr(app, "BASE_DIR", base_dir)
    monkeypatch.setattr(app, "INCOMING_DIR", base_dir / "Incoming")
    monkeypatch.setattr(app, "PASSED_DIR", base_dir / "Passed")
    monkeypatch.setattr(app, "NEEDS_FIX_DIR", base_dir / "Needs_Fix")
    monkeypatch.setattr(app, "REPORTS_DIR", base_dir / "Reports")
    monkeypatch.setattr(app, "LOGS_DIR", base_dir / "Logs")
    monkeypatch.setattr(app, "REJECTED_DIR", base_dir / "Rejected")

    app.ensure_directories()

    assert (base_dir / "Incoming").is_dir()
    assert (base_dir / "Passed").is_dir()
    assert (base_dir / "Needs_Fix").is_dir()
    assert (base_dir / "Reports").is_dir()
    assert (base_dir / "Logs").is_dir()
    assert (base_dir / "Rejected").is_dir()

def test_build_job_id_format():
    job_id = app.build_job_id()

    assert re.match(r"^job_\d{8}_\d{6}_[0-9a-f]{8}$", job_id)

def test_extract_company_job_number_from_start():
    assert app.extract_company_job_number("123456_customer_job.pdf") == "123456"
    assert app.extract_company_job_number("123456 customer_job.pdf") == "123456"
    assert app.extract_company_job_number("674591-customer_job.pdf") == "674591"

def test_extract_company_job_number_not_start():
    assert app.extract_company_job_number("00001_customer_job.pdf") is None
    assert app.extract_company_job_number("customer_123456_job.pdf") is None
    assert app.extract_company_job_number("job_123456.pdf") is None

def test_relative_to_base_returns_posix_relative_path(tmp_path, monkeypatch):
    base_dir = tmp_path / "Preflight_System"
    report = base_dir / "Reports" / "report.pdf"

    monkeypatch.setattr(app, "BASE_DIR", base_dir)

    assert app.relative_to_base(report) == "Reports/report.pdf"

def test_create_job_metadata_uses_minimal_pending_shape(tmp_path):
    pdf = tmp_path / "123456 CompanyBrochure.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    metadata = app.create_job_metadata(pdf)

    assert metadata["schemaVersion"] == 1
    assert metadata["jobId"].startswith("job_")
    assert metadata["source"] == "manual_drop"
    assert metadata["originalFilename"] == "123456 CompanyBrochure.pdf"
    assert metadata["storedFilename"] == "123456 CompanyBrochure.pdf"
    assert metadata["companyJobNumber"] == "123456"
    assert metadata["status"] == "Pending"
    assert metadata["printReady"] is None
    assert metadata["fileSizeBytes"] == pdf.stat().st_size
    assert metadata["finalPdfPath"] is None
    assert metadata["reportPath"] is None
    assert metadata["summary"] is None
    assert metadata["issues"] == []
    assert metadata["warnings"] == []
    assert metadata["errorMessage"] is None


def test_create_job_metadata_initializes_trim_bleed(tmp_path):
    pdf = tmp_path / "NoJobNumber.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    metadata = app.create_job_metadata(pdf)

    assert metadata["companyJobNumber"] is None
    assert metadata["trimBleed"] == {
        "trimStatus": None,
        "bleedStatus": None,
        "trimDetails": None,
        "bleedDetails": None,
    }

def test_write_job_metadata_creates_json_file(tmp_path, monkeypatch):
    metadata_dir = tmp_path / "Metadata"
    monkeypatch.setattr(app, "METADATA_DIR", metadata_dir)

    metadata = {
        "schemaVersion": 1,
        "jobId": "job_20240604_101530_abcd1234",
        "status": "Pending",
        "originalFilename": "test.pdf",
    }

    metadata_path = app.write_job_metadata(metadata)

    assert metadata_path == metadata_dir / "job_20240604_101530_abcd1234.json"
    assert metadata_path.exists()

    with open(metadata_path, "r", encoding="utf-8") as f:
        saved_metadata = app.json.load(f)
    
    assert saved_metadata == metadata

def test_load_job_metadata_reads_existing_json(tmp_path, monkeypatch):
    metadata_dir = tmp_path / "Metadata"
    metadata_dir.mkdir()
    monkeypatch.setattr(app, "METADATA_DIR", metadata_dir)

    metadata = {
        "schemaVersion": 1,
        "jobId": "job_20260604_101530_abcd1234",
        "status": "Pending",
    }

    metadata_path = metadata_dir / "job_20260604_101530_abcd1234.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        app.json.dump(metadata, f)

    loaded = app.load_job_metadata("job_20260604_101530_abcd1234")

    assert loaded == metadata

def test_load_job_metadata_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "METADATA_DIR", tmp_path / "Metadata")

    assert app.load_job_metadata("missing_job") is None

def test_update_job_metadata_updates_fields_and_writes_file(tmp_path, monkeypatch):
    metadata_dir = tmp_path / "Metadata"
    monkeypatch.setattr(app, "METADATA_DIR", metadata_dir)

    metadata = {
        "schemaVersion": 1,
        "jobId": "job_20260604_101530_abcd1234",
        "status": "Pending",
        "printReady": None,
    }

    metadata_path = app.update_job_metadata(
        metadata,
        status="Processing",
        printReady=False,
    )

    loaded = app.load_job_metadata("job_20260604_101530_abcd1234")

    assert metadata_path == metadata_dir / "job_20260604_101530_abcd1234.json"
    assert loaded["status"] == "Processing"
    assert loaded["printReady"] is False

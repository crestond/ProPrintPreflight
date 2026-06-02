# Basic test suite for ProPrintPreflight. This is not meant to be exhaustive, but it should cover the basics of the preflight process.

from pathlib import Path

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

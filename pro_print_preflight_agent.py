#!/usr/bin/env python3
"""
Pro Print Preflight Agent V4.0 — Print Shop Mode

What it does
------------
- Watches a Google Drive Incoming folder for PDFs
- Auto-detects job presets from the filename (no popup required)
- Runs print preflight checks:
  * trim / bleed
  * full PDF box diagnostics
  * placed-image DPI
  * empty page detection
  * CMYK / RGB image detection
  * page count validation
  * simple tri-fold / panel warning logic
  * overprint inspection placeholder
- Produces a PDF report
- Decides PRINT READY: YES / NO
- Moves files to Passed or Needs_Fix
- Writes a CSV log
- Optional Slack / email notifications
- Writes runtime logs for app debugging

Dependencies
------------
python3 -m pip install pymupdf reportlab requests

Run
---
python3 preflight_agent_v4_0.py
"""

from __future__ import annotations

import csv
from html import escape
import json
import logging
import math
import shutil
import smtplib
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    import requests  # optional, only needed for Slack notifications
except Exception:
    requests = None


# =========================
# LOGGING
# =========================

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SCRIPT_DIR = get_app_dir()
LOG_FILE = SCRIPT_DIR / "pro_print_runtime.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logging.info("Pro Print Preflight Agent starting...")


# =========================
# CONFIG
# =========================

CONFIG_FILE = SCRIPT_DIR / "config.json"

# ---- Folder structure ----
BASE_DIR = SCRIPT_DIR / "Preflight_System"
INCOMING_DIR = BASE_DIR / "Incoming"
PASSED_DIR = BASE_DIR / "Passed"
NEEDS_FIX_DIR = BASE_DIR / "Needs_Fix"
REPORTS_DIR = BASE_DIR / "Reports"
LOGS_DIR = BASE_DIR / "Logs"
REJECTED_DIR = BASE_DIR / "Rejected"  # optional add-on target, not used by default

WATCH_INTERVAL_SECONDS = 5
PROCESS_ONCE = False  # True = process existing PDFs once and exit, False = keep watching
FILE_STABLE_SECONDS = 2
FILE_READY_TIMEOUT_SECONDS = 60
LARGE_PDF_WARNING_MB = 250

# ---- Print rules ----
DEFAULT_BLEED_IN = 0.125
DEFAULT_SAFETY_MARGIN_IN = 0.125
SIZE_TOLERANCE_IN = 0.03
BLEED_TOLERANCE_IN = 0.03
MIN_DPI = 225
TARGET_DPI = 300
STRICT_RGB_FAIL = False  # False = RGB is warning, True = RGB is fail
FAIL_ON_EMPTY_PAGE = True
FAIL_ON_PAGE_COUNT_MISMATCH = True
FAIL_ON_TRIM_MISMATCH = True
FAIL_ON_BLEED_MISMATCH = True
FAIL_ON_OVERPRINT = False
TRIFOLD_EQUAL_PANEL_WARNING = True
AUTO_RENAME_PRINT_READY = False
AUTO_REJECT_FAILED = False
BRANDED_REPORT_MODE = True

# ---- Notifications ----
ENABLE_SLACK = False
SLACK_WEBHOOK_URL = ""
ENABLE_EMAIL = False
EMAIL_FROM = ""
EMAIL_TO = [""]
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = ""
SMTP_PASSWORD = ""

# ---- Job preset detection ----
# Matching is based on filename keywords, case-insensitive.
PRESETS = {
    "postcard_5x7": {
        "keywords": ["postcard", "5x7", "5 x 7"],
        "trim": (5.0, 7.0),
        "bleed": 0.125,
        "expected_pages": 2,
        "product_type": "double_sided",
        "fold_type": None,
        "safety": 0.125,
    },
    "postcard_4x6": {
        "keywords": ["postcard", "4x6", "4 x 6"],
        "trim": (4.0, 6.0),
        "bleed": 0.125,
        "expected_pages": 2,
        "product_type": "double_sided",
        "fold_type": None,
        "safety": 0.125,
    },
    "flyer_8.5x11_single": {
        "keywords": ["flyer", "8.5x11", "8.5 x 11", "single-sided flyer", "single sided flyer"],
        "trim": (8.5, 11.0),
        "bleed": 0.125,
        "expected_pages": 1,
        "product_type": "single_sided",
        "fold_type": None,
        "safety": 0.125,
    },
    "flyer_8.5x11_double": {
        "keywords": ["flyer", "8.5x11", "8.5 x 11", "double-sided flyer", "double sided flyer"],
        "trim": (8.5, 11.0),
        "bleed": 0.125,
        "expected_pages": 2,
        "product_type": "double_sided",
        "fold_type": None,
        "safety": 0.125,
    },
    "trifold_8.5x11": {
        "keywords": ["trifold", "tri-fold", "tri fold", "brochure", "8.5x11", "8.5 x 11"],
        "trim": (11.0, 8.5),
        "bleed": 0.125,
        "expected_pages": 2,
        "product_type": "double_sided",
        "fold_type": "trifold_letter",
        "safety": 0.125,
    },
    "booklet_8.5x11": {
        "keywords": ["booklet", "8.5x11", "8.5 x 11"],
        "trim": (8.5, 11.0),
        "bleed": 0.125,
        "expected_pages": None,
        "product_type": "multipage",
        "fold_type": None,
        "safety": 0.125,
    },
}

DEFAULT_PRESET = {
    "name": "generic_print_job",
    "trim": None,
    "bleed": DEFAULT_BLEED_IN,
    "expected_pages": None,
    "product_type": "generic",
    "fold_type": None,
    "safety": DEFAULT_SAFETY_MARGIN_IN,
}


def _resolve_config_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def _config_bool(config: Dict[str, Any], key: str, current: bool) -> bool:
    value = config.get(key, config.get(key.upper(), current))
    return value if isinstance(value, bool) else current


def _config_int(config: Dict[str, Any], key: str, current: int) -> int:
    value = config.get(key, config.get(key.upper(), current))
    if isinstance(value, bool):
        return current
    try:
        return int(value)
    except (TypeError, ValueError):
        return current


def load_config() -> None:
    """Load optional config.json without changing built-in defaults on failure."""
    global BASE_DIR, INCOMING_DIR, PASSED_DIR, NEEDS_FIX_DIR, REPORTS_DIR, LOGS_DIR, REJECTED_DIR
    global WATCH_INTERVAL_SECONDS, PROCESS_ONCE, FILE_STABLE_SECONDS, FILE_READY_TIMEOUT_SECONDS
    global LARGE_PDF_WARNING_MB
    global MIN_DPI, TARGET_DPI, STRICT_RGB_FAIL, AUTO_RENAME_PRINT_READY, AUTO_REJECT_FAILED
    global ENABLE_SLACK, SLACK_WEBHOOK_URL, ENABLE_EMAIL, EMAIL_FROM, EMAIL_TO
    global SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD

    if not CONFIG_FILE.exists():
        logging.info(f"No config.json found. Using default BASE_DIR: {BASE_DIR.resolve()}")
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as exc:
        logging.exception(f"Could not load config.json. Using defaults. Error: {exc}")
        return

    if not isinstance(config, dict):
        logging.error("config.json must contain a JSON object. Using defaults.")
        return

    base_dir_value = config.get("base_dir", config.get("BASE_DIR"))
    if isinstance(base_dir_value, str) and base_dir_value.strip():
        BASE_DIR = _resolve_config_path(base_dir_value)

    INCOMING_DIR = BASE_DIR / "Incoming"
    PASSED_DIR = BASE_DIR / "Passed"
    NEEDS_FIX_DIR = BASE_DIR / "Needs_Fix"
    REPORTS_DIR = BASE_DIR / "Reports"
    LOGS_DIR = BASE_DIR / "Logs"
    REJECTED_DIR = BASE_DIR / "Rejected"

    WATCH_INTERVAL_SECONDS = max(1, _config_int(config, "watch_interval_seconds", WATCH_INTERVAL_SECONDS))
    PROCESS_ONCE = _config_bool(config, "process_once", PROCESS_ONCE)
    FILE_STABLE_SECONDS = max(1, _config_int(config, "file_stable_seconds", FILE_STABLE_SECONDS))
    FILE_READY_TIMEOUT_SECONDS = max(
        FILE_STABLE_SECONDS + 1,
        _config_int(config, "file_ready_timeout_seconds", FILE_READY_TIMEOUT_SECONDS),
    )
    LARGE_PDF_WARNING_MB = max(1, _config_int(config, "large_pdf_warning_mb", LARGE_PDF_WARNING_MB))

    MIN_DPI = _config_int(config, "min_dpi", MIN_DPI)
    TARGET_DPI = _config_int(config, "target_dpi", TARGET_DPI)
    STRICT_RGB_FAIL = _config_bool(config, "strict_rgb_fail", STRICT_RGB_FAIL)
    AUTO_RENAME_PRINT_READY = _config_bool(config, "auto_rename_print_ready", AUTO_RENAME_PRINT_READY)
    AUTO_REJECT_FAILED = _config_bool(config, "auto_reject_failed", AUTO_REJECT_FAILED)

    ENABLE_SLACK = _config_bool(config, "enable_slack", ENABLE_SLACK)
    SLACK_WEBHOOK_URL = str(config.get("slack_webhook_url", SLACK_WEBHOOK_URL) or "")
    ENABLE_EMAIL = _config_bool(config, "enable_email", ENABLE_EMAIL)
    EMAIL_FROM = str(config.get("email_from", EMAIL_FROM) or "")
    email_to = config.get("email_to", EMAIL_TO)
    if isinstance(email_to, list):
        EMAIL_TO = [str(item) for item in email_to if str(item)]
    elif isinstance(email_to, str):
        EMAIL_TO = [email_to]
    SMTP_HOST = str(config.get("smtp_host", SMTP_HOST) or SMTP_HOST)
    SMTP_PORT = _config_int(config, "smtp_port", SMTP_PORT)
    SMTP_USERNAME = str(config.get("smtp_username", SMTP_USERNAME) or "")
    SMTP_PASSWORD = str(config.get("smtp_password", SMTP_PASSWORD) or "")

    logging.info(f"Loaded config.json: {CONFIG_FILE}")


# =========================
# DATA STRUCTURES
# =========================

@dataclass
class CheckResult:
    status: str  # PASS / WARNING / FAIL / INFO
    details: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PageDiagnostics:
    page_number: int
    trim_size: Optional[Tuple[float, float]]
    bleed_size: Optional[Tuple[float, float]]
    crop_size: Optional[Tuple[float, float]]
    media_size: Optional[Tuple[float, float]]
    rect_size: Optional[Tuple[float, float]]
    dpi_records: List[Dict[str, Any]] = field(default_factory=list)
    page_has_content: bool = True
    image_color_spaces: List[str] = field(default_factory=list)


# =========================
# HELPERS
# =========================

def ensure_directories() -> None:
    logging.info(f"Resolved BASE_DIR: {BASE_DIR.resolve()}")
    for folder in [INCOMING_DIR, PASSED_DIR, NEEDS_FIX_DIR, REPORTS_DIR, LOGS_DIR, REJECTED_DIR]:
        logging.info(f"Using folder: {folder.resolve()}")
        folder.mkdir(parents=True, exist_ok=True)


def inches_from_points(value: float) -> float:
    return value / 72.0


def round2(value: float) -> float:
    return round(value, 3)


def size_tuple_from_rect(rect: Optional[fitz.Rect]) -> Optional[Tuple[float, float]]:
    if rect is None:
        return None
    return (round2(inches_from_points(rect.width)), round2(inches_from_points(rect.height)))


def normalize_size(size: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    if not size:
        return None
    w, h = size
    return tuple(sorted((round2(w), round2(h))))


def sizes_match(
    a: Optional[Tuple[float, float]],
    b: Optional[Tuple[float, float]],
    tolerance: float = SIZE_TOLERANCE_IN,
) -> bool:
    if not a or not b:
        return False
    a_n = normalize_size(a)
    b_n = normalize_size(b)
    return abs(a_n[0] - b_n[0]) <= tolerance and abs(a_n[1] - b_n[1]) <= tolerance


def format_size(size: Optional[Tuple[float, float]]) -> str:
    if not size:
        return "not available"
    return f"{size[0]:.3f} x {size[1]:.3f}"


def format_image_location(record: Dict[str, Any]) -> str:
    x_in = record.get("x_in")
    y_in = record.get("y_in")
    if x_in is None or y_in is None:
        return "not available"
    return f"x {x_in:.3f} in, y {y_in:.3f} in"


def format_file_size(size_bytes: int) -> str:
    size_mb = size_bytes / (1024 * 1024)
    if size_mb >= 1024:
        return f"{size_mb / 1024:.2f} GB"
    return f"{size_mb:.2f} MB"


def detect_preset_from_filename(filename: str) -> Dict[str, Any]:
    lower_name = filename.lower()
    best_name = None
    best_score = -1

    for preset_name, preset in PRESETS.items():
        score = sum(1 for keyword in preset["keywords"] if keyword.lower() in lower_name)
        if score > best_score and score > 0:
            best_name = preset_name
            best_score = score

    if best_name:
        matched = PRESETS[best_name].copy()
        matched["name"] = best_name
        return matched

    return DEFAULT_PRESET.copy()


def choose_trim_rect(page: fitz.Page) -> Tuple[Optional[fitz.Rect], str]:
    try:
        trim = page.trimbox
        if trim and trim.width > 0 and trim.height > 0:
            return trim, "TrimBox"
    except Exception:
        pass

    try:
        crop = page.cropbox
        if crop and crop.width > 0 and crop.height > 0:
            return crop, "CropBox"
    except Exception:
        pass

    try:
        media = page.mediabox
        if media and media.width > 0 and media.height > 0:
            return media, "MediaBox"
    except Exception:
        pass

    try:
        rect = page.rect
        if rect and rect.width > 0 and rect.height > 0:
            return rect, "rect"
    except Exception:
        pass

    return None, "none"


def choose_bleed_rect(page: fitz.Page) -> Tuple[Optional[fitz.Rect], str]:
    try:
        bleed = page.bleedbox
        if bleed and bleed.width > 0 and bleed.height > 0:
            return bleed, "BleedBox"
    except Exception:
        pass

    try:
        media = page.mediabox
        if media and media.width > 0 and media.height > 0:
            return media, "MediaBox"
    except Exception:
        pass

    return None, "none"


def get_page_box_diagnostics(page: fitz.Page) -> Dict[str, Optional[Tuple[float, float]]]:
    diag = {}

    for name, getter in [
        ("trimbox", lambda p: p.trimbox),
        ("bleedbox", lambda p: p.bleedbox),
        ("cropbox", lambda p: p.cropbox),
        ("mediabox", lambda p: p.mediabox),
        ("rect", lambda p: p.rect),
    ]:
        try:
            diag[name] = size_tuple_from_rect(getter(page))
        except Exception:
            diag[name] = None

    return diag


def page_has_meaningful_content(page: fitz.Page) -> bool:
    try:
        if page.get_text("text").strip():
            return True
    except Exception:
        pass

    try:
        drawings = page.get_drawings()
        if drawings:
            return True
    except Exception:
        pass

    try:
        images = page.get_images(full=True)
        if images:
            return True
    except Exception:
        pass

    return False


def classify_colorspace_name(components: Optional[int], colorspace_name: Optional[str]) -> str:
    name = (colorspace_name or "").upper()
    if "CMYK" in name or components == 4:
        return "CMYK"
    if "RGB" in name or components == 3:
        return "RGB"
    if "GRAY" in name or components == 1:
        return "GRAY"
    if components == 0 or components is None:
        return "UNKNOWN"
    return f"OTHER({components})"


def compute_image_dpi(page: fitz.Page, img_xref: int, img_width_px: int, img_height_px: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    try:
        image_rects = page.get_image_rects(img_xref, transform=False)
    except Exception:
        image_rects = []

    for rect in image_rects:
        page_rect = page.rect
        width_in = inches_from_points(rect.width) if rect.width else 0
        height_in = inches_from_points(rect.height) if rect.height else 0
        x_in = inches_from_points(rect.x0 - page_rect.x0)
        y_in = inches_from_points(rect.y0 - page_rect.y0)

        x_dpi = (img_width_px / width_in) if width_in > 0 else 0
        y_dpi = (img_height_px / height_in) if height_in > 0 else 0
        effective_dpi = min(x_dpi, y_dpi) if x_dpi and y_dpi else max(x_dpi, y_dpi)

        records.append({
            "placed_width_in": round2(width_in),
            "placed_height_in": round2(height_in),
            "x_in": round2(x_in),
            "y_in": round2(y_in),
            "pixel_width": img_width_px,
            "pixel_height": img_height_px,
            "x_dpi": round(x_dpi) if x_dpi else 0,
            "y_dpi": round(y_dpi) if y_dpi else 0,
            "effective_dpi": round(effective_dpi) if effective_dpi else 0,
        })

    return records


def safe_move_file(src: Path, dst_dir: Path, new_name: Optional[str] = None) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / (new_name or src.name)

    counter = 1
    stem = target.stem
    suffix = target.suffix
    while target.exists():
        target = dst_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    shutil.move(str(src), str(target))
    return target


def append_csv_log(row: Dict[str, Any]) -> None:
    log_file = LOGS_DIR / "preflight_log.csv"
    fieldnames = [
        "timestamp",
        "filename",
        "preset",
        "print_ready",
        "destination",
        "issues",
        "warnings",
        "min_dpi",
        "color_summary",
        "page_count",
    ]

    write_header = not log_file.exists()
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def send_slack_notification(message: str) -> None:
    if not ENABLE_SLACK or not SLACK_WEBHOOK_URL:
        return
    if requests is None:
        logging.warning("Slack notification skipped: requests module not installed.")
        return

    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
        logging.info("Slack notification sent.")
    except Exception as exc:
        logging.error(f"Slack notification failed: {exc}")


def send_email_notification(subject: str, body: str) -> None:
    if not ENABLE_EMAIL or not EMAIL_FROM or not SMTP_USERNAME or not SMTP_PASSWORD or not any(EMAIL_TO):
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = ", ".join([x for x in EMAIL_TO if x])
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        logging.info("Email notification sent.")
    except Exception as exc:
        logging.error(f"Email notification failed: {exc}")


# =========================
# CHECKS
# =========================

def check_trim(page_diags: List[PageDiagnostics], preset: Dict[str, Any]) -> CheckResult:
    expected_trim = preset.get("trim")
    if not expected_trim:
        return CheckResult(
            status="INFO",
            details="No preset trim defined. Trim recorded for diagnostics only.",
            data={"expected_trim": None},
        )

    bad_pages = []
    for p in page_diags:
        if not sizes_match(p.trim_size, expected_trim):
            bad_pages.append({
                "page": p.page_number,
                "found": p.trim_size,
                "expected": expected_trim,
            })

    if bad_pages:
        status = "FAIL" if FAIL_ON_TRIM_MISMATCH else "WARNING"
        details = "Trim size mismatch on page(s): " + ", ".join(
            f"{b['page']} (found {format_size(b['found'])}, expected {format_size(b['expected'])})"
            for b in bad_pages
        )
        return CheckResult(
            status=status,
            details=details,
            data={"bad_pages": bad_pages, "expected_trim": expected_trim},
        )

    return CheckResult(
        status="PASS",
        details=f"Trim matches expected preset size: {format_size(expected_trim)}.",
        data={"expected_trim": expected_trim},
    )


def check_bleed(page_diags: List[PageDiagnostics], preset: Dict[str, Any]) -> CheckResult:
    expected_trim = preset.get("trim")
    expected_bleed = preset.get("bleed", DEFAULT_BLEED_IN)

    if not expected_trim:
        return CheckResult(
            status="INFO",
            details="No preset trim defined. Bleed recorded for diagnostics only.",
            data={"expected_bleed": expected_bleed},
        )

    expected_bleed_size = (
        round2(expected_trim[0] + expected_bleed * 2),
        round2(expected_trim[1] + expected_bleed * 2),
    )
    bad_pages = []
    missing_bleed_pages = []

    for p in page_diags:
        if not p.bleed_size:
            missing_bleed_pages.append(p.page_number)
            continue
        if not sizes_match(p.bleed_size, expected_bleed_size, BLEED_TOLERANCE_IN):
            bad_pages.append({
                "page": p.page_number,
                "found": p.bleed_size,
                "expected": expected_bleed_size,
            })

    if missing_bleed_pages:
        status = "FAIL" if FAIL_ON_BLEED_MISMATCH else "WARNING"
        return CheckResult(
            status=status,
            details="No bleed detected on page(s): " + ", ".join(map(str, missing_bleed_pages)),
            data={"expected_bleed_size": expected_bleed_size, "missing_pages": missing_bleed_pages},
        )

    if bad_pages:
        status = "FAIL" if FAIL_ON_BLEED_MISMATCH else "WARNING"
        details = "Bleed size mismatch on page(s): " + ", ".join(
            f"{b['page']} (found {format_size(b['found'])}, expected {format_size(b['expected'])})"
            for b in bad_pages
        )
        return CheckResult(
            status=status,
            details=details,
            data={"bad_pages": bad_pages, "expected_bleed_size": expected_bleed_size},
        )

    return CheckResult(
        status="PASS",
        details=f"Bleed matches expected size: {format_size(expected_bleed_size)}.",
        data={"expected_bleed_size": expected_bleed_size},
    )


def check_dpi(page_diags: List[PageDiagnostics]) -> CheckResult:
    all_records = []
    low_records = []
    min_found = None

    for p in page_diags:
        for rec in p.dpi_records:
            dpi = rec.get("effective_dpi", 0)
            all_records.append({"page": p.page_number, **rec})
            if min_found is None or dpi < min_found:
                min_found = dpi
            if dpi and dpi < MIN_DPI:
                low_records.append({"page": p.page_number, **rec})

    if not all_records:
        return CheckResult(
            status="INFO",
            details="No placed raster images found for DPI analysis.",
            data={"min_dpi": None},
        )

    if low_records:
        details = "Low effective DPI detected: " + ", ".join(
            f"page {r['page']} ({r['effective_dpi']} dpi, {format_image_location(r)})"
            for r in low_records[:12]
        )
        return CheckResult(
            status="FAIL",
            details=details,
            data={"min_dpi": min_found, "low_records": low_records},
        )

    warning_records = [r for r in all_records if MIN_DPI <= r.get("effective_dpi", 0) < TARGET_DPI]
    if warning_records:
        details = "Some images are printable but below ideal target DPI: " + ", ".join(
            f"page {r['page']} ({r['effective_dpi']} dpi)" for r in warning_records[:12]
        )
        return CheckResult(
            status="WARNING",
            details=details,
            data={"min_dpi": min_found, "warning_records": warning_records},
        )

    return CheckResult(
        status="PASS",
        details=f"All placed raster images meet target resolution. Lowest found: {min_found} dpi.",
        data={"min_dpi": min_found},
    )


def check_empty_pages(page_diags: List[PageDiagnostics]) -> CheckResult:
    empty_pages = [p.page_number for p in page_diags if not p.page_has_content]
    if empty_pages:
        status = "FAIL" if FAIL_ON_EMPTY_PAGE else "WARNING"
        return CheckResult(
            status=status,
            details="Empty page(s) detected: " + ", ".join(map(str, empty_pages)),
            data={"empty_pages": empty_pages},
        )
    return CheckResult(status="PASS", details="No empty pages detected.", data={})


def check_color(page_diags: List[PageDiagnostics]) -> CheckResult:
    found = []
    for p in page_diags:
        for cs in p.image_color_spaces:
            found.append((p.page_number, cs))

    if not found:
        return CheckResult(
            status="INFO",
            details="No raster images found for CMYK/RGB inspection.",
            data={"color_summary": []},
        )

    unique_spaces = sorted({cs for _, cs in found})
    rgb_pages = sorted({page for page, cs in found if cs == "RGB"})
    cmyk_pages = sorted({page for page, cs in found if cs == "CMYK"})
    other_pages = sorted({page for page, cs in found if cs not in {"RGB", "CMYK", "GRAY"}})

    if rgb_pages:
        status = "FAIL" if STRICT_RGB_FAIL else "WARNING"
        details = (
            f"RGB raster content detected on page(s): {', '.join(map(str, rgb_pages))}. "
            f"Color spaces seen: {', '.join(unique_spaces)}."
        )
        return CheckResult(
            status=status,
            details=details,
            data={
                "color_summary": unique_spaces,
                "rgb_pages": rgb_pages,
                "cmyk_pages": cmyk_pages,
                "other_pages": other_pages,
            },
        )

    if other_pages:
        return CheckResult(
            status="WARNING",
            details=(
                f"Non-standard raster color spaces detected on page(s): {', '.join(map(str, other_pages))}. "
                f"Color spaces seen: {', '.join(unique_spaces)}."
            ),
            data={"color_summary": unique_spaces},
        )

    return CheckResult(
        status="PASS",
        details=f"Raster image color spaces are print-safe: {', '.join(unique_spaces)}.",
        data={"color_summary": unique_spaces, "cmyk_pages": cmyk_pages},
    )


def check_overprint(doc: fitz.Document) -> CheckResult:
    # Placeholder for V4.0. PyMuPDF does not reliably expose PDF overprint flags across all content types.
    # This keeps the architecture ready without giving fake certainty.
    return CheckResult(
        status="INFO",
        details="Overprint inspection is not fully supported in this V4.0 parser. Manual review recommended.",
        data={"manual_review_recommended": True},
    )


def check_page_count(doc: fitz.Document, preset: Dict[str, Any]) -> CheckResult:
    expected = preset.get("expected_pages")
    actual = len(doc)

    if expected is None:
        return CheckResult(
            status="INFO",
            details=f"Page count recorded: {actual}. No preset page count requirement.",
            data={"actual": actual, "expected": None},
        )

    if actual != expected:
        status = "FAIL" if FAIL_ON_PAGE_COUNT_MISMATCH else "WARNING"
        return CheckResult(
            status=status,
            details=f"Page count mismatch. Found {actual}, expected {expected}.",
            data={"actual": actual, "expected": expected},
        )

    return CheckResult(
        status="PASS",
        details=f"Page count matches preset: {actual} page(s).",
        data={"actual": actual, "expected": expected},
    )


def check_fold_and_panels(page_diags: List[PageDiagnostics], preset: Dict[str, Any]) -> CheckResult:
    fold_type = preset.get("fold_type")
    if not fold_type:
        return CheckResult(status="INFO", details="No fold logic required for this preset.", data={})

    if fold_type == "trifold_letter":
        issues = []
        first = page_diags[0] if page_diags else None
        if not first or not first.trim_size:
            return CheckResult(
                status="WARNING",
                details="Tri-fold preset detected, but trim size could not be read for panel logic.",
                data={},
            )

        trim = normalize_size(first.trim_size)
        expected = normalize_size((8.5, 11.0))
        if not sizes_match(trim, expected):
            issues.append("Tri-fold preset detected but flat size does not match expected 8.5 x 11 finished layout.")

        if TRIFOLD_EQUAL_PANEL_WARNING:
            issues.append(
                "Tri-fold jobs should usually account for a slightly narrower fold-in panel. "
                "Verify panel widths manually if artwork was built as equal thirds."
            )

        if issues:
            return CheckResult(status="WARNING", details=" ".join(issues), data={"fold_type": fold_type})

        return CheckResult(
            status="PASS",
            details="Tri-fold preset detected. Flat size is consistent; manual panel-width review still recommended.",
            data={"fold_type": fold_type},
        )

    return CheckResult(
        status="INFO",
        details=f"Fold preset recognized ({fold_type}) but no specific rule set exists yet.",
        data={"fold_type": fold_type},
    )


# =========================
# VERDICT ENGINE
# =========================

def get_print_ready_status(results: Dict[str, CheckResult]) -> Tuple[str, List[str], List[str]]:
    fails = [name for name, result in results.items() if result.status == "FAIL"]
    warnings = [name for name, result in results.items() if result.status == "WARNING"]
    return ("NO" if fails else "YES", fails, warnings)


# =========================
# PDF ANALYSIS
# =========================

def analyze_pdf(pdf_path: Path) -> Dict[str, Any]:
    preset = detect_preset_from_filename(pdf_path.name)
    doc = fitz.open(pdf_path)
    page_diags: List[PageDiagnostics] = []

    for i, page in enumerate(doc, start=1):
        boxes = get_page_box_diagnostics(page)
        trim_rect, _trim_source = choose_trim_rect(page)
        bleed_rect, _bleed_source = choose_bleed_rect(page)

        diag = PageDiagnostics(
            page_number=i,
            trim_size=size_tuple_from_rect(trim_rect),
            bleed_size=size_tuple_from_rect(bleed_rect),
            crop_size=boxes.get("cropbox"),
            media_size=boxes.get("mediabox"),
            rect_size=boxes.get("rect"),
            page_has_content=page_has_meaningful_content(page),
        )

        try:
            images = page.get_images(full=True)
        except Exception:
            images = []

        for img in images:
            try:
                xref = img[0]
                width_px = int(img[2])
                height_px = int(img[3])
                colorspace_name = img[5] if len(img) > 5 else ""
                components = None
                try:
                    pix = fitz.Pixmap(doc, xref)
                    components = getattr(pix, "n", None)
                    pix = None
                except Exception:
                    pass

                cs = classify_colorspace_name(components, colorspace_name)
                diag.image_color_spaces.append(cs)
                diag.dpi_records.extend(compute_image_dpi(page, xref, width_px, height_px))
            except Exception:
                continue

        page_diags.append(diag)

    results: Dict[str, CheckResult] = {
        "trim": check_trim(page_diags, preset),
        "bleed": check_bleed(page_diags, preset),
        "dpi": check_dpi(page_diags),
        "empty_pages": check_empty_pages(page_diags),
        "color": check_color(page_diags),
        "overprint": check_overprint(doc),
        "pages": check_page_count(doc, preset),
        "fold": check_fold_and_panels(page_diags, preset),
    }

    print_ready, fails, warnings = get_print_ready_status(results)
    doc.close()

    return {
        "file": pdf_path,
        "preset": preset,
        "page_diags": page_diags,
        "results": results,
        "print_ready": print_ready,
        "fails": fails,
        "warnings": warnings,
    }


# =========================
# REPORT GENERATION
# =========================

def build_brand_header(print_ready: str) -> List[Paragraph]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BrandTitle",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        spaceAfter=8,
    )
    body_style = styles["BodyText"]

    if print_ready == "YES":
        lines = [
            Paragraph("PRO PRINT PREFLIGHT REPORT", title_style),
            Paragraph("JOB STATUS: PRINT READY", body_style),
            Paragraph("DEPARTMENT: PREPRESS AUTOMATION", body_style),
        ]
    else:
        lines = [
            Paragraph("PRO PRINT PREFLIGHT REPORT", title_style),
            Paragraph("JOB STATUS: NEEDS ATTENTION", body_style),
            Paragraph("DEPARTMENT: PREPRESS AUTOMATION", body_style),
        ]
    return lines


def report_cell(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value)), style)


def generate_pdf_report(analysis: Dict[str, Any]) -> Path:
    pdf_path: Path = analysis["file"]
    report_name = f"{pdf_path.stem}_report.pdf"
    report_path = REPORTS_DIR / report_name

    doc = SimpleDocTemplate(
        str(report_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    story = []
    report_title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=23,
        spaceAfter=6,
    )
    section_heading_style = ParagraphStyle(
        "ReportSectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        spaceBefore=10,
        spaceAfter=6,
    )
    page_heading_style = ParagraphStyle(
        "ReportPageHeading",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        spaceBefore=8,
        spaceAfter=3,
    )
    summary_style = ParagraphStyle(
        "ReportSummary",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
    )
    table_header_style = ParagraphStyle(
        "ReportTableHeader",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10.5,
        wordWrap="CJK",
    )
    table_cell_style = ParagraphStyle(
        "ReportTableCell",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        wordWrap="CJK",
    )

    if BRANDED_REPORT_MODE:
        story.extend(build_brand_header(analysis["print_ready"]))
        story.append(Spacer(1, 0.2 * inch))

    title = Paragraph(f"Preflight Report - {escape(pdf_path.name)}", report_title_style)
    story.append(title)
    story.append(Spacer(1, 0.15 * inch))

    subtitle = Paragraph(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>"
        f"Preset: {analysis['preset'].get('name', 'generic_print_job')}<br/>"
        f"PRINT READY: <b>{analysis['print_ready']}</b>",
        summary_style,
    )
    story.append(subtitle)
    story.append(Spacer(1, 0.16 * inch))

    story.append(Paragraph("Trim And Bleed Summary", section_heading_style))
    box_table_data = [[
        report_cell("Page", table_header_style),
        report_cell("TrimBox / Chosen Trim", table_header_style),
        report_cell("BleedBox / Fallback Bleed", table_header_style),
    ]]
    for p in analysis["page_diags"]:
        box_table_data.append([
            report_cell(p.page_number, table_cell_style),
            report_cell(format_size(p.trim_size), table_cell_style),
            report_cell(format_size(p.bleed_size), table_cell_style),
        ])

    box_table = Table(box_table_data, colWidths=[0.65 * inch, 3.25 * inch, 3.25 * inch], repeatRows=1)
    box_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]))
    story.append(box_table)
    story.append(Spacer(1, 0.22 * inch))

    story.append(Paragraph("Check Summary", section_heading_style))
    result_table_data = [[
        report_cell("Check", table_header_style),
        report_cell("Status", table_header_style),
        report_cell("Details", table_header_style),
    ]]
    for check_name, result in analysis["results"].items():
        result_table_data.append([
            report_cell(check_name.replace("_", " ").title(), table_cell_style),
            report_cell(result.status, table_cell_style),
            report_cell(result.details, table_cell_style),
        ])

    table = Table(result_table_data, colWidths=[1.15 * inch, 0.85 * inch, 5.3 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.32 * inch))

    story.append(Paragraph("Additional Page Diagnostics", section_heading_style))
    for p in analysis["page_diags"]:
        story.append(Paragraph(f"Page {p.page_number}", page_heading_style))
        text = (
            f"CropBox: {format_size(p.crop_size)}<br/>"
            f"MediaBox: {format_size(p.media_size)}<br/>"
            f"rect: {format_size(p.rect_size)}<br/>"
            f"Has content: {'YES' if p.page_has_content else 'NO'}<br/>"
            f"Image color spaces: {', '.join(p.image_color_spaces) if p.image_color_spaces else 'none'}"
        )
        story.append(Paragraph(text, summary_style))

        if p.dpi_records:
            story.append(Spacer(1, 0.08 * inch))
            dpi_rows = [[
                report_cell("Placed Size", table_header_style),
                report_cell("Pixels", table_header_style),
                report_cell("Effective DPI", table_header_style),
                report_cell("Location", table_header_style),
            ]]
            for rec in p.dpi_records[:20]:
                dpi_rows.append([
                    report_cell(f"{rec['placed_width_in']} x {rec['placed_height_in']} in", table_cell_style),
                    report_cell(f"{rec['pixel_width']} x {rec['pixel_height']}", table_cell_style),
                    report_cell(rec["effective_dpi"], table_cell_style),
                    report_cell(format_image_location(rec), table_cell_style),
                ])
            dpi_table = Table(dpi_rows, colWidths=[1.6 * inch, 1.45 * inch, 1.2 * inch, 2.05 * inch], repeatRows=1)
            dpi_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(dpi_table)
        else:
            story.append(Paragraph("No raster image placement records found.", styles["BodyText"]))

        story.append(Spacer(1, 0.18 * inch))

    doc.build(story)
    return report_path


# =========================
# ACTIONS
# =========================

def maybe_rename_print_ready_file(src_path: Path, analysis: Dict[str, Any]) -> str:
    if not AUTO_RENAME_PRINT_READY or analysis["print_ready"] != "YES":
        return src_path.name
    safe_stem = src_path.stem.replace(" ", "_")
    return f"{safe_stem}_PRINT_READY{src_path.suffix}"


def build_notification_message(analysis: Dict[str, Any], moved_to: Path, report_path: Path) -> str:
    results = analysis["results"]
    min_dpi = results["dpi"].data.get("min_dpi")
    color_summary = ", ".join(results["color"].data.get("color_summary", [])) or "n/a"
    warning_text = ", ".join(analysis["warnings"]) if analysis["warnings"] else "none"
    fail_text = ", ".join(analysis["fails"]) if analysis["fails"] else "none"

    return textwrap.dedent(f"""
    Preflight Complete
    File: {analysis['file'].name}
    Preset: {analysis['preset'].get('name')}
    PRINT READY: {analysis['print_ready']}
    Destination: {moved_to}
    Report: {report_path}
    Fails: {fail_text}
    Warnings: {warning_text}
    Min DPI: {min_dpi}
    Colors: {color_summary}
    """).strip()


def process_pdf(pdf_path: Path) -> None:
    process_start = time.monotonic()
    try:
        file_size_bytes = pdf_path.stat().st_size
    except OSError:
        file_size_bytes = 0

    logging.info(f"Processing PDF: {pdf_path.name} ({format_file_size(file_size_bytes)})")
    if file_size_bytes >= LARGE_PDF_WARNING_MB * 1024 * 1024:
        logging.warning(
            f"Large PDF detected: {pdf_path.name} is {format_file_size(file_size_bytes)} "
            f"(warning threshold: {LARGE_PDF_WARNING_MB} MB). Processing may take longer."
        )

    try:
        analysis_start = time.monotonic()
        analysis = analyze_pdf(pdf_path)
        logging.info(f"Analysis completed for {pdf_path.name} in {time.monotonic() - analysis_start:.2f}s")

        report_start = time.monotonic()
        report_path = generate_pdf_report(analysis)
        logging.info(f"Report generation completed for {pdf_path.name} in {time.monotonic() - report_start:.2f}s")

        if analysis["print_ready"] == "YES":
            new_name = maybe_rename_print_ready_file(pdf_path, analysis)
            moved_to = safe_move_file(pdf_path, PASSED_DIR, new_name=new_name)
            destination_label = "Passed"
        else:
            if AUTO_REJECT_FAILED:
                moved_to = safe_move_file(pdf_path, REJECTED_DIR)
                destination_label = "Rejected"
            else:
                moved_to = safe_move_file(pdf_path, NEEDS_FIX_DIR)
                destination_label = "Needs_Fix"

        logging.info(f"PRINT READY: {analysis['print_ready']}")
        logging.info(f"Moved to: {moved_to}")
        logging.info(f"Report written: {report_path}")

        results = analysis["results"]
        append_csv_log({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "filename": analysis["file"].name,
            "preset": analysis["preset"].get("name", "generic_print_job"),
            "print_ready": analysis["print_ready"],
            "destination": destination_label,
            "issues": "; ".join(analysis["fails"]),
            "warnings": "; ".join(analysis["warnings"]),
            "min_dpi": results["dpi"].data.get("min_dpi"),
            "color_summary": ", ".join(results["color"].data.get("color_summary", [])),
            "page_count": results["pages"].data.get("actual"),
        })

        message = build_notification_message(analysis, moved_to, report_path)
        send_slack_notification(message)
        send_email_notification(f"Preflight Result - {analysis['file'].name}", message)
        logging.info(f"Finished processing {moved_to.name} in {time.monotonic() - process_start:.2f}s")

    except Exception as exc:
        logging.exception(f"Failed to process {pdf_path.name} after {time.monotonic() - process_start:.2f}s: {exc}")


# =========================
# WATCHER
# =========================

def get_incoming_pdfs() -> List[Path]:
    return sorted([p for p in INCOMING_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])


def wait_until_file_is_ready(
    pdf_path: Path,
    stable_seconds: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
) -> bool:
    """Return True once size and mtime stop changing long enough to process safely."""
    stable_seconds = max(stable_seconds or FILE_STABLE_SECONDS, 1)
    timeout_seconds = max(timeout_seconds or FILE_READY_TIMEOUT_SECONDS, stable_seconds + 1)
    deadline = time.time() + timeout_seconds
    stable_since: Optional[float] = None
    last_signature: Optional[Tuple[int, float]] = None

    while time.time() < deadline:
        try:
            stat = pdf_path.stat()
            signature = (stat.st_size, stat.st_mtime)

            with open(pdf_path, "rb"):
                pass

            if stat.st_size <= 0:
                stable_since = None
            elif signature == last_signature:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= max(stable_seconds, 1):
                    return True
            else:
                stable_since = None
                last_signature = signature

        except FileNotFoundError:
            logging.info(f"File disappeared before processing: {pdf_path}")
            return False
        except OSError as exc:
            logging.info(f"Waiting for file to become available: {pdf_path.name} ({exc})")
            stable_since = None

        time.sleep(1)

    logging.warning(f"Timed out waiting for file to finish copying: {pdf_path}")
    return False


def watch_loop() -> None:
    load_config()
    ensure_directories()
    logging.info("Preflight Agent V4.0 running.")
    logging.info(f"Watching folder: {INCOMING_DIR}")

    processed_recently: Dict[str, float] = {}

    while True:
        try:
            pdfs = get_incoming_pdfs()
            if pdfs:
                for pdf in pdfs:
                    key = str(pdf.resolve())
                    mtime = pdf.stat().st_mtime
                    last_seen = processed_recently.get(key)
                    if last_seen is not None and math.isclose(last_seen, mtime, rel_tol=0.0, abs_tol=0.0):
                        continue

                    if not wait_until_file_is_ready(pdf):
                        continue

                    processed_recently[key] = pdf.stat().st_mtime
                    process_pdf(pdf)

                if PROCESS_ONCE:
                    logging.info("PROCESS_ONCE enabled. Exiting after one pass.")
                    break

            time.sleep(WATCH_INTERVAL_SECONDS)

        except Exception as exc:
            logging.exception(f"Watcher loop error: {exc}")
            time.sleep(WATCH_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        watch_loop()
    except KeyboardInterrupt:
        logging.info("Preflight Agent stopped by user.")
        sys.exit(0)

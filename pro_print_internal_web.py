#!/usr/bin/env python3
"""
Small internal upload/status UI for Pro Print Preflight.

This intentionally uses the Python standard library only. The preflight agent
remains the worker; this app only accepts uploads, creates initial metadata, and
shows recent job status records.
"""

from __future__ import annotations

import html
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import pro_print_preflight_agent as agent


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
UPLOAD_STAGING_DIR_NAME = "Upload_Staging"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
JOB_FILENAME_RE = re.compile(r"^(job_\d{8}_\d{6}_[0-9a-f]{8})__(.+)$")


def get_configured_bind_address(default_host: str = DEFAULT_HOST, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    host = default_host
    port = default_port

    try:
        with open(agent.CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return host, port

    if not isinstance(config, dict):
        return host, port

    web_config = config.get("web", {})
    if not isinstance(web_config, dict):
        return host, port

    configured_host = web_config.get("host")
    if isinstance(configured_host, str) and configured_host.strip():
        host = configured_host.strip()

    try:
        configured_port = int(web_config.get("port", port))
        if 1 <= configured_port <= 65535:
            port = configured_port
    except (TypeError, ValueError):
        pass

    return host, port


def sanitize_original_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = SAFE_FILENAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "upload.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def build_stored_upload_filename(job_id: str, original_filename: str) -> str:
    return f"{job_id}__{sanitize_original_filename(original_filename)}"


def display_filename_from_stored(stored_filename: str) -> str:
    match = JOB_FILENAME_RE.match(stored_filename)
    return match.group(2) if match else stored_filename


def metadata_sort_key(metadata: Dict[str, Any]) -> str:
    return str(
        metadata.get("processingFinishedAt")
        or metadata.get("processingStartedAt")
        or metadata.get("createdAt")
        or ""
    )


def load_recent_jobs(limit: int = 100) -> List[Dict[str, Any]]:
    agent.METADATA_DIR.mkdir(parents=True, exist_ok=True)
    jobs: List[Dict[str, Any]] = []

    for metadata_path in agent.METADATA_DIR.glob("*.json"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as exc:
            logging.warning(f"Skipping unreadable metadata file {metadata_path.name}: {exc}")
            continue

        if isinstance(metadata, dict):
            jobs.append(metadata)

    jobs.sort(key=metadata_sort_key, reverse=True)
    return jobs[:limit]


def create_upload_metadata(job_id: str, original_filename: str, stored_filename: str, file_size_bytes: int) -> Dict[str, Any]:
    incoming_path = agent.INCOMING_DIR / stored_filename
    return agent.create_job_metadata(
        incoming_path,
        job_id=job_id,
        source="internal_ui",
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_size_bytes=file_size_bytes,
    )


def write_upload_to_incoming(original_filename: str, body_reader, content_length: int) -> Dict[str, Any]:
    if content_length <= 0:
        raise ValueError("Upload is empty.")
    if content_length > MAX_UPLOAD_BYTES:
        raise ValueError("Upload is too large.")
    if not original_filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF uploads are accepted.")
    storage_ok, storage_reason = agent.has_min_free_space(required_bytes=content_length)
    if not storage_ok:
        raise ValueError(storage_reason)

    safe_original = sanitize_original_filename(original_filename)

    job_id = agent.build_job_id()
    stored_filename = build_stored_upload_filename(job_id, safe_original)
    staging_dir = agent.BASE_DIR / UPLOAD_STAGING_DIR_NAME
    staging_dir.mkdir(parents=True, exist_ok=True)
    agent.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    temp_path = staging_dir / f"{job_id}.uploading"
    final_path = agent.INCOMING_DIR / stored_filename

    bytes_remaining = content_length
    with open(temp_path, "wb") as f:
        while bytes_remaining > 0:
            chunk = body_reader.read(min(CHUNK_SIZE, bytes_remaining))
            if not chunk:
                raise ValueError("Upload ended before all bytes were received.")
            f.write(chunk)
            bytes_remaining -= len(chunk)

    metadata = create_upload_metadata(job_id, safe_original, stored_filename, content_length)
    agent.write_job_metadata(metadata)
    temp_path.replace(final_path)
    return metadata


def render_index_html() -> bytes:
    return b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pro Print Preflight</title>
  <style>
    :root { color-scheme: light; font-family: Arial, Helvetica, sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #1f2933; }
    header { background: #111827; color: white; padding: 18px 28px; }
    h1 { font-size: 22px; margin: 0; font-weight: 700; letter-spacing: 0; }
    main { max-width: 1120px; margin: 0 auto; padding: 24px; }
    .upload { border: 2px dashed #8a96a8; background: white; border-radius: 8px; padding: 34px; text-align: center; }
    .upload.dragover { border-color: #0f766e; background: #eefcf8; }
    .upload button { border: 0; border-radius: 6px; background: #0f766e; color: white; padding: 10px 16px; font-size: 15px; cursor: pointer; }
    .upload p { margin: 12px 0 0; color: #536173; }
    .bar { display: flex; justify-content: space-between; align-items: center; margin: 24px 0 10px; gap: 12px; }
    .bar h2 { font-size: 18px; margin: 0; }
    .bar button { border: 1px solid #b7c0cc; border-radius: 6px; background: white; padding: 8px 12px; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8dee7; }
    th, td { padding: 11px 12px; border-bottom: 1px solid #e6eaf0; text-align: left; vertical-align: top; font-size: 14px; }
    th { background: #eef1f5; font-size: 12px; text-transform: uppercase; color: #526070; }
    tr:last-child td { border-bottom: 0; }
    .status { font-weight: 700; }
    .Passed { color: #087443; }
    .Needs_Fix, .Rejected, .Error { color: #b42318; }
    .Processing, .Pending { color: #8a5a00; }
    .muted { color: #64748b; }
    .message { margin-top: 12px; min-height: 22px; color: #334155; }
    a { color: #0f5f9f; }
  </style>
</head>
<body>
  <header><h1>Pro Print Preflight</h1></header>
  <main>
    <section id="dropZone" class="upload">
      <input id="fileInput" type="file" accept="application/pdf,.pdf" multiple hidden>
      <button id="pickFiles" type="button">Choose PDFs</button>
      <p>Drag PDF files here to submit them for preflight.</p>
      <div id="message" class="message"></div>
    </section>
    <section>
      <div class="bar">
        <h2>Recent Jobs</h2>
        <button id="refresh" type="button">Refresh</button>
      </div>
      <table>
        <thead><tr><th>File</th><th>Status</th><th>Trim/Bleed</th><th>Created</th><th>Report</th></tr></thead>
        <tbody id="jobs"><tr><td colspan="5" class="muted">Loading...</td></tr></tbody>
      </table>
    </section>
  </main>
  <script>
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const message = document.getElementById('message');
    const jobs = document.getElementById('jobs');
    document.getElementById('pickFiles').onclick = () => fileInput.click();
    document.getElementById('refresh').onclick = loadJobs;
    fileInput.onchange = () => uploadFiles(fileInput.files);
    ['dragenter', 'dragover'].forEach(name => dropZone.addEventListener(name, event => {
      event.preventDefault();
      dropZone.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(name => dropZone.addEventListener(name, event => {
      event.preventDefault();
      dropZone.classList.remove('dragover');
    }));
    dropZone.addEventListener('drop', event => uploadFiles(event.dataTransfer.files));

    async function uploadFiles(files) {
      const pdfs = [...files].filter(file => file.name.toLowerCase().endsWith('.pdf'));
      if (!pdfs.length) {
        message.textContent = 'Choose one or more PDF files.';
        return;
      }
      for (const file of pdfs) {
        message.textContent = `Uploading ${file.name}...`;
        const response = await fetch(`/api/upload?filename=${encodeURIComponent(file.name)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/pdf' },
          body: file
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({ error: 'Upload failed.' }));
          message.textContent = error.error || 'Upload failed.';
          return;
        }
      }
      message.textContent = 'Upload complete. Waiting for preflight processing.';
      fileInput.value = '';
      loadJobs();
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }

    function statusLabel(status) {
      return String(status || 'Pending').replaceAll('_', ' ');
    }

    function checkLabel(status) {
      const labels = {
        PASS: 'Pass',
        WARNING: 'Warning',
        FAIL: 'Needs Review',
        INFO: 'Info Only'
      };
      return labels[status] || status || null;
    }

    function trimBleedLabel(trimBleed) {
      const trimStatus = checkLabel(trimBleed?.trimStatus);
      const bleedStatus = checkLabel(trimBleed?.bleedStatus);
      if (!trimStatus && !bleedStatus) {
        return 'Pending';
      }
      const pages = trimBleed?.pages || [];
      const firstPage = pages[0] || {};
      const pageCount = pages.length;
      const actualValues = [];
      if (firstPage.trim) {
        actualValues.push(`Trim: ${firstPage.trim}`);
      }
      if (firstPage.bleed) {
        actualValues.push(`Bleed: ${firstPage.bleed}`);
      }
      if (pageCount > 1 && actualValues.length) {
        actualValues.push(`${pageCount} pages checked`);
      }
      const trimDetails = trimBleed?.trimDetails || 'No trim details yet.';
      const bleedDetails = trimBleed?.bleedDetails || 'No bleed details yet.';
      const checkDetails = `Trim check (${trimStatus || 'Pending'}): ${trimDetails}\nBleed check (${bleedStatus || 'Pending'}): ${bleedDetails}`;
      return actualValues.length ? `${actualValues.join('\\n')}\\n${checkDetails}` : checkDetails;
    }

    async function loadJobs() {
      const response = await fetch('/api/jobs');
      const data = await response.json();
      const rows = data.jobs || [];
      if (!rows.length) {
        jobs.innerHTML = '<tr><td colspan="5" class="muted">No jobs yet.</td></tr>';
        return;
      }
      jobs.innerHTML = rows.map(job => {
        const trim = job.trimBleed || {};
        const trimBleed = trimBleedLabel(trim);
        const displayStatus = statusLabel(job.status);
        const report = job.reportPath ? `<a href="/files/${encodeURIComponent(job.reportPath)}">Report</a>` : '<span class="muted">Not ready</span>';
        return `<tr>
          <td>${escapeHtml(job.originalFilename || job.storedFilename)}</td>
          <td class="status ${escapeHtml(job.status)}">${escapeHtml(displayStatus)}</td>
          <td>${escapeHtml(trimBleed).replaceAll('\\n', '<br>')}</td>
          <td>${escapeHtml(job.createdAt || '')}</td>
          <td>${report}</td>
        </tr>`;
      }).join('');
    }

    loadJobs();
    setInterval(loadJobs, 5000);
  </script>
</body>
</html>
"""


class ProPrintWebHandler(BaseHTTPRequestHandler):
    server_version = "ProPrintInternalWeb/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(render_index_html(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/jobs":
            self.send_json({"jobs": load_recent_jobs()})
            return
        if parsed.path.startswith("/files/"):
            self.send_preflight_file(unquote(parsed.path.removeprefix("/files/")))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/upload":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        filename = parse_qs(parsed.query).get("filename", ["upload.pdf"])[0]
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            metadata = write_upload_to_incoming(filename, self.rfile, content_length)
        except Exception as exc:
            logging.exception(f"Upload failed: {exc}")
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_json({"jobId": metadata["jobId"], "status": metadata["status"]})

    def send_preflight_file(self, relative_path: str) -> None:
        try:
            target = (agent.BASE_DIR / relative_path).resolve()
            target.relative_to(agent.BASE_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{html.escape(target.name)}"')
        self.end_headers()
        with open(target, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_bytes(body, "application/json", status=status)

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logging.info("Internal UI: " + format, *args)


def run_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    agent.load_config()
    agent.ensure_directories()
    if host is None or port is None:
        configured_host, configured_port = get_configured_bind_address()
        host = configured_host if host is None else host
        port = configured_port if port is None else port
    server = ThreadingHTTPServer((host, port), ProPrintWebHandler)
    logging.info(f"Pro Print internal UI running at http://{host}:{port}/")
    print(f"Pro Print internal UI running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Pro Print internal UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()

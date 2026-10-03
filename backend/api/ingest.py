"""POST /ingest and GET /documents/{id}."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import settings
from pipelines.ingestion.extractors import ExtractionError
from pipelines.ingestion.extractors.url_extractor import assert_public_host

router = APIRouter()

MAX_BYTES = 100 * 1024 * 1024  # 100 MB
EXT_TO_TYPE = {".pdf": "pdf", ".docx": "docx", ".csv": "csv",
               ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image"}
SIGNATURES = {"pdf": b"%PDF", "docx": b"PK"}
QUEUE = "queue:ingest"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _parse_uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


async def _read_limited(upload) -> bytes | None:
    data, total = [], 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_BYTES:
            return None
        data.append(chunk)
    return b"".join(data)


@router.post("/ingest")
async def ingest(request: Request):
    pool, arq = request.app.state.pool, request.app.state.arq
    content_type = request.headers.get("content-type", "")
    pipeline_id = None
    file_bytes: bytes | None = None
    url: str | None = None
    original_name = ""

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return _error(400, "invalid_request", "Provide a 'file' field")
        original_name = os.path.basename(upload.filename or "upload")
        ext = Path(original_name).suffix.lower()
        source_type = EXT_TO_TYPE.get(ext)
        if source_type is None:
            return _error(415, "unsupported_media_type",
                          f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(EXT_TO_TYPE))}")
        file_bytes = await _read_limited(upload)
        if file_bytes is None:
            return _error(413, "payload_too_large", "File exceeds 100 MB")
        if not file_bytes:
            return _error(400, "invalid_request", "File is empty")
        sig = SIGNATURES.get(source_type)
        if sig and not file_bytes.startswith(sig):
            return _error(415, "unsupported_media_type", f"File content is not a valid {source_type}")
        pipeline_id = form.get("pipeline_id")
    elif content_type.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "invalid_request", "Body is not valid JSON")
        url = (body or {}).get("url")
        if not url or not isinstance(url, str):
            return _error(400, "invalid_request", "JSON body needs a 'url'")
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ExtractionError("Only http(s) URLs are supported")
            await assert_public_host(parsed.hostname)
        except ExtractionError as exc:
            return _error(422, "validation_error", str(exc))
        source_type, original_name = "url", url
        pipeline_id = body.get("pipeline_id")
    else:
        return _error(415, "unsupported_media_type", "Use multipart/form-data or application/json")

    if pipeline_id is not None:
        pipeline_id = _parse_uuid(pipeline_id)
        if pipeline_id is None:
            return _error(422, "validation_error", "pipeline_id must be a UUID")

    # Deduplication: hash of file bytes (or of the URL string for web pages)
    content_hash = hashlib.sha256(file_bytes if file_bytes is not None else f"url:{url}".encode()).hexdigest()
    existing = await pool.fetchrow("SELECT id, status FROM documents WHERE content_hash=$1", content_hash)
    if existing:
        return JSONResponse(status_code=200, content={
            "document_id": str(existing["id"]), "status": existing["status"], "duplicate": True})

    document_id = str(uuid.uuid4())
    try:
        await pool.execute(
            "INSERT INTO documents (id, filename, source_type, content_hash, metadata, pipeline_id, status) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6, 'queued')",
            document_id, original_name, source_type, content_hash, json.dumps({}), pipeline_id)
    except asyncpg.UniqueViolationError:  # lost a race with an identical upload
        existing = await pool.fetchrow("SELECT id, status FROM documents WHERE content_hash=$1", content_hash)
        return JSONResponse(status_code=200, content={
            "document_id": str(existing["id"]), "status": existing["status"], "duplicate": True})

    try:
        if file_bytes is not None:
            upload_dir = Path(settings.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_ext = re.sub(r"[^.a-z0-9]", "", Path(original_name).suffix.lower())
            source = str(upload_dir / f"{document_id}{safe_ext}")
            Path(source).write_bytes(file_bytes)
        else:
            source = url
        await arq.enqueue_job("process_document", document_id, source, source_type,
                              _queue_name=QUEUE, _job_id=document_id)
    except Exception as exc:
        await pool.execute("UPDATE documents SET status='failed', metadata = metadata || $2::jsonb WHERE id=$1",
                           document_id, json.dumps({"error": f"enqueue failed: {exc}"}))
        return _error(503, "provider_unavailable", "Could not queue the document; try again")

    return JSONResponse(status_code=202, content={
        "document_id": document_id, "status": "queued", "duplicate": False})


@router.get("/documents/{document_id}")
async def get_document(document_id: str, request: Request):
    doc_id = _parse_uuid(document_id)
    if doc_id is None:
        return _error(404, "not_found", "Document not found")
    row = await request.app.state.pool.fetchrow(
        "SELECT id, filename, source_type, status, chunk_count, metadata, created_at "
        "FROM documents WHERE id=$1", doc_id)
    if row is None:
        return _error(404, "not_found", "Document not found")
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {"document_id": str(row["id"]), "filename": row["filename"],
            "source_type": row["source_type"], "status": row["status"],
            "chunk_count": row["chunk_count"], "metadata": metadata,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None}

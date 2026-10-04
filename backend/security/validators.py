"""Input validation & sanitization: HTML stripping, lengths, URLs (SSRF), file types."""
import ipaddress
import re
import socket
from urllib.parse import urlparse

import bleach
from fastapi import HTTPException, UploadFile

MAX_QUERY_LEN = 5000
MAX_PIPELINE_NAME_LEN = 100
MAX_URL_LEN = 2048
URL_RE = re.compile(r"^https?://")


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


# ---------- text ----------
def sanitize_text(text: str) -> str:
    """Strip all HTML before storing."""
    return bleach.clean(text, tags=[], strip=True)


def validate_query_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise _bad("Query must not be empty")
    if len(text) > MAX_QUERY_LEN:
        raise _bad(f"Query exceeds {MAX_QUERY_LEN} characters")
    return sanitize_text(text)


def validate_pipeline_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise _bad("Pipeline name must not be empty")
    if len(name) > MAX_PIPELINE_NAME_LEN:
        raise _bad(f"Pipeline name exceeds {MAX_PIPELINE_NAME_LEN} characters")
    return sanitize_text(name).strip()


# ---------- URLs / SSRF ----------
def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_document_url(url: str) -> str:
    """Must match ^https?:// and must not resolve to private/loopback addresses."""
    if not isinstance(url, str) or len(url) > MAX_URL_LEN:
        raise _bad("Invalid URL")
    if not URL_RE.match(url):
        raise _bad("URL must start with http:// or https://")
    host = urlparse(url).hostname
    if not host:
        raise _bad("URL has no hostname")
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(".localhost"):
        raise _bad("URL points to a blocked address")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise _bad("URL hostname could not be resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if _ip_blocked(ip):
            raise _bad("URL points to a blocked (private/internal) address")
    return url


# ---------- files ----------
# extension -> (allowed MIME types, magic-byte prefixes)
FILE_RULES = {
    ".pdf": ({"application/pdf"}, [b"%PDF-"]),
    ".docx": (
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        [b"PK\x03\x04"],
    ),
    ".txt": ({"text/plain"}, None),   # None => must look like text
    ".md": ({"text/markdown", "text/plain", "text/x-markdown"}, None),
}

EXECUTABLE_MAGIC = [
    (b"MZ", "Windows executable"),
    (b"\x7fELF", "ELF executable"),
    (b"\xcf\xfa\xed\xfe", "Mach-O executable"),
    (b"\xce\xfa\xed\xfe", "Mach-O executable"),
    (b"\xca\xfe\xba\xbe", "Mach-O/Java class"),
    (b"#!", "script with shebang"),
]


def validate_upload(filename: str, content_type: str | None, head: bytes) -> str:
    """Check extension, declared MIME AND magic bytes. Returns the extension."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in FILE_RULES:
        raise _bad(f"File type '{ext or 'none'}' is not allowed")
    allowed_mime, magics = FILE_RULES[ext]
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in allowed_mime:
        raise _bad(f"MIME type '{mime}' does not match extension {ext}")

    for sig, label in EXECUTABLE_MAGIC:
        if head.startswith(sig):
            raise _bad(f"File content is a {label}, not a {ext} file")

    if magics is not None:
        if not any(head.startswith(m) for m in magics):
            raise _bad(f"File content does not match {ext} format (magic bytes mismatch)")
    else:
        if b"\x00" in head:
            raise _bad("Binary content in a text file")
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            # allow truncation mid-character at the end of the sample
            if e.start < len(head) - 4:
                raise _bad("Text file is not valid UTF-8")
    return ext


async def validate_upload_file(upload: UploadFile) -> str:
    """FastAPI helper: reads first 8 KB, validates, rewinds the stream."""
    head = await upload.read(8192)
    await upload.seek(0)
    return validate_upload(upload.filename or "", upload.content_type, head)

"""Task 13 - Security hardening package for NeuroFlow."""
from .auth import router as auth_router, get_current_user, require_scope
from .middleware import SecurityHeadersMiddleware
from .prompt_injection import (
    QueryRejected,
    check_query,
    scan_for_injection,
    flag_chunks,
    set_llm_classifier,
    query_rejected_handler,
)
from .secret_detector import redact_secrets, scan_for_secrets
from .validators import (
    sanitize_text,
    validate_query_text,
    validate_pipeline_name,
    validate_document_url,
    validate_upload,
)


def register_security(app):
    """One-call wiring: middleware, auth router, query-rejected handler."""
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(auth_router)
    app.add_exception_handler(QueryRejected, query_rejected_handler)

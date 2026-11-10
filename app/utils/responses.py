"""Standard JSON response envelope.

Contract used by every endpoint:

    success: {"success": true,  "data": <payload>, "message": <optional>}
    failure: {"success": false, "code": <CODE>,    "message": <text>}

List endpoints must return their rows plus a pagination block inside ``data``:

    {"success": true, "data": {"<resource>": [...], "pagination": {...}}}
"""

from flask import jsonify


def ok(data=None, message=None, status=200, **extra):
    """Build a success response."""
    payload = {"success": True}
    if data is not None:
        from app.services.product_translation_service import display_response
        payload["data"] = display_response(data)
    if message:
        payload["message"] = message
    payload.update(extra)
    return jsonify(payload), status


def created(data=None, message="创建成功", **extra):
    return ok(data, message, status=201, **extra)


def fail(message, status=400, code="BAD_REQUEST", **extra):
    """Build an error response (used by the global error handlers)."""
    payload = {"success": False, "code": code, "message": message}
    payload.update(extra)
    return jsonify(payload), status


def paginate(items, total, page, per_page, **extra):
    """Standard pagination block."""
    pages = (total + per_page - 1) // per_page if per_page else 0
    payload = {
        "items": items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
            "has_prev": page > 1,
            "has_next": page < pages,
        },
    }
    payload.update(extra)
    return payload

import json
from uuid import UUID

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .authentication import AuthenticationError, authenticate_request
from .catalog import get_catalog
from .models import ChatMessage, ChatSession
from .orchestrator import handle_message


def _error(message: str, status: int, code: str = "REQUEST_ERROR") -> JsonResponse:
    return JsonResponse(
        {"success": False, "message": message, "code": code},
        status=status,
    )


def _body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


def _principal_or_error(request):
    try:
        return authenticate_request(request), None
    except AuthenticationError as exc:
        return None, _error(str(exc), 401, "UNAUTHORIZED")


def _owned_session(session_id: UUID, principal: dict) -> ChatSession | None:
    return ChatSession.objects.filter(
        id=session_id,
        principal_type=principal["principal_type"],
        principal_id=principal["id"],
        deleted_at__isnull=True,
    ).first()


@require_http_methods(["GET"])
def health_live(request):
    return JsonResponse({"status": "OK", "message": "Chatbot service is running"})


@require_http_methods(["GET"])
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        catalog_count = len(get_catalog())
        return JsonResponse(
            {
                "status": "OK",
                "message": "Chatbot service is ready",
                "catalogEntries": catalog_count,
            }
        )
    except DatabaseError as exc:
        return _error(f"Database is not ready: {exc.__class__.__name__}", 503, "NOT_READY")


@require_http_methods(["GET", "POST"])
def sessions(request):
    principal, error = _principal_or_error(request)
    if error:
        return error

    if request.method == "GET":
        rows = ChatSession.objects.filter(
            principal_type=principal["principal_type"],
            principal_id=principal["id"],
            deleted_at__isnull=True,
        )[:100]
        return JsonResponse(
            {
                "success": True,
                "data": [
                    {
                        "id": str(row.id),
                        "title": row.title,
                        "created_at": row.created_at.isoformat(),
                        "updated_at": row.updated_at.isoformat(),
                    }
                    for row in rows
                ],
            }
        )

    try:
        body = _body(request)
        title = str(body.get("title", ""))[:200]
        session = ChatSession.objects.create(
            principal_type=principal["principal_type"],
            principal_id=principal["id"],
            title=title,
        )
        return JsonResponse(
            {
                "success": True,
                "data": {
                    "id": str(session.id),
                    "title": session.title,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                },
            },
            status=201,
        )
    except ValueError as exc:
        return _error(str(exc), 400, "BAD_REQUEST")


@require_http_methods(["GET", "DELETE"])
def session_detail(request, session_id: UUID):
    principal, error = _principal_or_error(request)
    if error:
        return error
    session = _owned_session(session_id, principal)
    if not session:
        return _error("Session not found", 404, "NOT_FOUND")

    if request.method == "DELETE":
        session.deleted_at = session.updated_at
        session.save(update_fields=["deleted_at", "updated_at"])
        return JsonResponse({"success": True, "message": "Session deleted"})

    return JsonResponse(
        {
            "success": True,
            "data": {
                "id": str(session.id),
                "title": session.title,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "message_count": session.messages.count(),
            },
        }
    )


@require_http_methods(["GET", "POST"])
def session_messages(request, session_id: UUID):
    principal, error = _principal_or_error(request)
    if error:
        return error
    session = _owned_session(session_id, principal)
    if not session:
        return _error("Session not found", 404, "NOT_FOUND")

    if request.method == "GET":
        messages = session.messages.all()[:100]
        return JsonResponse(
            {
                "success": True,
                "data": [
                    {
                        "id": str(message.id),
                        "role": message.role,
                        "content": message.content,
                        "plan": message.plan,
                        "citations": message.citations,
                        "created_at": message.created_at.isoformat(),
                    }
                    for message in messages
                ],
            }
        )

    try:
        body = _body(request)
        question = str(body.get("message", "")).strip()
        if not question:
            return _error("message is required", 400, "BAD_REQUEST")
        if len(question) > 8000:
            return _error("message must be at most 8000 characters", 400, "BAD_REQUEST")
        result = handle_message(session, question)
        return JsonResponse({"success": True, "data": result})
    except ValueError as exc:
        return _error(str(exc), 400, "BAD_REQUEST")
    except DatabaseError:
        return _error("Chatbot database operation failed", 503, "DATABASE_ERROR")
    except Exception as exc:  # Keep provider/SQL details out of the client response.
        return _error(f"Chatbot request failed: {exc.__class__.__name__}", 500, "CHATBOT_ERROR")


@require_http_methods(["GET"])
def catalog(request):
    _, error = _principal_or_error(request)
    if error:
        return error
    return JsonResponse(
        {
            "success": True,
            "data": [
                {
                    "table_name": entry["table_name"],
                    "crime_category": entry.get("crime_category", ""),
                    "row_count": entry.get("row_count", 0),
                    "columns": [column.get("name") or column.get("COLUMN_NAME") for column in entry.get("columns_schema", [])],
                }
                for entry in get_catalog()
            ],
        }
    )

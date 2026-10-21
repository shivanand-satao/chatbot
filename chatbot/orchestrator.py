import uuid

from django.db import transaction
from django.db.models import Max
from django.conf import settings

from .answerer import answer_from_rows
from .catalog import get_catalog, get_catalog_entry
from .executor import execute_query
from .models import ChatMessage, ChatSession
from .planner import heuristic_plan, plan_question
from .schemas import QueryPlan
from .sql_compiler import QueryPlanError, compile_plan


def _append_message(
    session: ChatSession,
    role: str,
    content: str,
    *,
    plan: dict | None = None,
    citations: list[dict] | None = None,
    request_id: uuid.UUID | None = None,
) -> ChatMessage:
    with transaction.atomic():
        locked_session = ChatSession.objects.select_for_update().get(pk=session.pk)
        current = (
            ChatMessage.objects.filter(session=locked_session).aggregate(Max("sequence"))["sequence__max"]
            or 0
        )
        message = ChatMessage.objects.create(
            session=locked_session,
            sequence=current + 1,
            role=role,
            content=content,
            plan=plan,
            citations=citations,
            request_id=request_id,
        )
        locked_session.save(update_fields=["updated_at"])
    return message


def handle_message(session: ChatSession, question: str) -> dict:
    request_id = uuid.uuid4()
    _append_message(session, "user", question, request_id=request_id)

    plan, candidates = plan_question(question)
    plan_data = plan.model_dump()
    if plan.intent == "clarification":
        answer = plan.clarification or "Please clarify which dataset or metric you want to query."
        assistant = _append_message(
            session,
            "assistant",
            answer,
            plan=plan_data,
            citations=[],
            request_id=request_id,
        )
        return {
            "request_id": str(request_id),
            "message_id": str(assistant.id),
            "answer": answer,
            "plan": plan_data,
            "citations": [],
            "needs_clarification": True,
        }

    catalog = get_catalog()
    entry = get_catalog_entry(plan.table or "", catalog) or (candidates[0] if candidates else None)
    if not entry:
        raise QueryPlanError("No catalog entry matched the planned dataset")

    try:
        sql, params = compile_plan(plan, entry, settings.DB_SCHEMA)
    except QueryPlanError:
        # A provider-generated plan is not trusted. Fall back to the local
        # planner before surfacing a hard failure to the user.
        fallback = heuristic_plan(question, candidates)
        if fallback.model_dump() == plan_data:
            raise
        plan = fallback
        plan_data = plan.model_dump()
        entry = get_catalog_entry(plan.table or "", catalog)
        if not entry:
            raise QueryPlanError("No catalog entry matched the fallback dataset")
        sql, params = compile_plan(plan, entry, settings.DB_SCHEMA)

    rows = execute_query(sql, params)
    answer = answer_from_rows(rows, entry["table_name"])
    citations = [
        {
            "table": entry["table_name"],
            "dimensions": plan.dimensions,
            "measures": [measure.model_dump() for measure in plan.measures],
            "filters": [filter_spec.model_dump() for filter_spec in plan.filters],
            "result_rows": len(rows),
        }
    ]
    assistant = _append_message(
        session,
        "assistant",
        answer,
        plan=plan_data,
        citations=citations,
        request_id=request_id,
    )
    return {
        "request_id": str(request_id),
        "message_id": str(assistant.id),
        "answer": answer,
        "plan": plan_data,
        "citations": citations,
        "needs_clarification": False,
    }

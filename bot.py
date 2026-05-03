"""
Vera Bot — FastAPI application for the magicpin AI Challenge.

Exposes the 5 required endpoints:
  GET  /v1/healthz   — liveness probe
  GET  /v1/metadata  — bot identity
  POST /v1/context   — receive context pushes
  POST /v1/tick      — periodic wake-up; bot initiates conversations
  POST /v1/reply     — handle merchant/customer replies
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from composer.engine import composer
from context.store import context_store
from conversation.state import conversation_registry
from models import (
    ComposedMessage,
    ContextPushRequest,
    ContextPushResponse,
    HealthzResponse,
    MetadataResponse,
    ReplyRequest,
    ReplyResponse,
    TickAction,
    TickRequest,
    TickResponse,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("bot")

app = FastAPI(title="Vera Bot", version="1.0.0")
START_TIME = time.time()


# ---------------------------------------------------------------------------
# Global exception handler — never crash the server
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": str(exc)[:200]},
    )


# ---------------------------------------------------------------------------
# GET /v1/healthz
# ---------------------------------------------------------------------------


@app.get("/v1/healthz")
async def healthz() -> dict:
    counts = context_store.counts()
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": {
            "category": counts.get("category", 0),
            "merchant": counts.get("merchant", 0),
            "customer": counts.get("customer", 0),
            "trigger": counts.get("trigger", 0),
        },
    }


# ---------------------------------------------------------------------------
# GET /v1/metadata
# ---------------------------------------------------------------------------


@app.get("/v1/metadata")
async def metadata() -> dict:
    return {
        "team_name": "Solo Rahul",
        "team_members": ["Rahul"],
        "model": "gemini-2.5-flash + llama-3.3-70b",
        "approach": (
            "Dual-LLM architecture: Gemini 2.5 Flash for message composition "
            "with per-trigger-kind prompt routing, Groq Llama 3.3 70B for "
            "lightweight intent classification. Token-bucket rate limiting, "
            "category-aware voice injection, auto-reply detection, and "
            "multi-turn conversation state tracking."
        ),
        "contact_email": "rahulkumarkeshri475@gmail.com",
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /v1/context
# ---------------------------------------------------------------------------


@app.post("/v1/context")
async def push_context(body: ContextPushRequest) -> dict:
    valid_scopes = {"category", "merchant", "customer", "trigger"}
    if body.scope not in valid_scopes:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": "invalid_scope",
                "details": f"Scope must be one of {valid_scopes}",
            },
        )

    accepted, current_version = context_store.upsert(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
    )

    if not accepted:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.now(timezone.utc).isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# POST /v1/tick
# ---------------------------------------------------------------------------


@app.post("/v1/tick")
async def tick(body: TickRequest) -> dict:
    # Pre-filter: resolve context and check suppression before spawning tasks
    eligible: list[tuple[str, dict, dict, dict | None, dict | None]] = []
    for trigger_id in body.available_triggers:
        trigger, merchant, category, customer = context_store.resolve_trigger(trigger_id)
        if not trigger or not merchant:
            logger.debug("Skipping trigger %s — missing context", trigger_id)
            continue
        suppression_key = trigger.get("suppression_key", "")
        if suppression_key and context_store.is_suppressed(suppression_key):
            logger.debug("Skipping trigger %s — suppressed", trigger_id)
            continue
        eligible.append((trigger_id, trigger, merchant, category, customer))

    if not eligible:
        return {"actions": []}

    # Compose all eligible triggers in parallel
    async def _compose_one(
        trigger_id: str,
        trigger: dict,
        merchant: dict,
        category: dict | None,
        customer: dict | None,
    ) -> dict | None:
        try:
            composed = await composer.compose(
                category=category,
                merchant=merchant,
                trigger=trigger,
                customer=customer,
            )
        except Exception as e:
            logger.error("Composition failed for trigger %s: %s", trigger_id, e)
            return None

        if not composed or not composed.body:
            return None

        merchant_id = trigger.get("merchant_id", "")
        customer_id = trigger.get("customer_id")
        conv_id = f"conv_{merchant_id}_{trigger_id}"
        suppression_key = trigger.get("suppression_key", "")

        conv = conversation_registry.get_or_create(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trigger_id,
        )

        # Guard: never send the same body twice in the same conversation
        if conv.is_verbatim_repeat(composed.body):
            logger.warning(
                "Verbatim repeat detected for %s [%s] — skipping tick action",
                merchant_id, trigger_id,
            )
            return None

        conv.add_bot_turn(composed.body)
        context_store.mark_sent(suppression_key)

        identity = merchant.get("identity", {})
        owner = identity.get("owner_first_name", identity.get("name", ""))

        logger.info(
            "Composed message for %s [%s]: %s",
            merchant_id,
            trigger.get("kind", "?"),
            composed.body[:80],
        )
        return {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed.send_as,
            "trigger_id": trigger_id,
            "template_name": f"vera_{trigger.get('kind', 'generic')}_v1",
            "template_params": [owner, composed.body[:50]],
            "body": composed.body,
            "cta": composed.cta,
            "suppression_key": suppression_key,
            "rationale": composed.rationale,
        }

    results = await asyncio.gather(
        *[_compose_one(*args) for args in eligible],
        return_exceptions=True,
    )

    actions = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Tick composition task raised: %s", r)
        elif r is not None:
            actions.append(r)

    return {"actions": actions}


# ---------------------------------------------------------------------------
# POST /v1/reply
# ---------------------------------------------------------------------------


@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> dict:
    # Get or create conversation state
    conv = conversation_registry.get_or_create(
        conversation_id=body.conversation_id,
        merchant_id=body.merchant_id or "",
    )

    # Record the merchant's message
    conv.add_merchant_turn(
        body=body.message,
        turn_number=body.turn_number,
        timestamp=body.received_at,
    )

    # Step 1: Quick heuristic checks (no LLM needed)
    # IMPORTANT: Check intent_action FIRST — a merchant saying "yes" must never
    # be misclassified as auto-reply. Order matters.
    if conv.detect_intent_action(body.message):
        intent = "intent_action"
        logger.info(
            "[%s] Intent detected: intent_action (positive merchant signal)",
            body.conversation_id,
        )
    elif conv.detect_auto_reply(body.message):
        # End immediately — auto-replies are not real engagement
        conv.ended = True
        logger.info(
            "[%s] Auto-reply detected — ending conversation",
            body.conversation_id,
        )
        return {
            "action": "end",
            "rationale": (
                "Auto-reply detected — ending to avoid wasting turns "
                "on a bot/auto-responder."
            ),
        }
    elif conv.detect_not_interested(body.message):
        conv.ended = True
        logger.info(
            "[%s] Not-interested signal detected — ending conversation",
            body.conversation_id,
        )
        return {
            "action": "end",
            "rationale": "Merchant expressed disinterest — respecting their decision.",
        }
    else:
        # Step 2: Use LLM classifier for ambiguous messages
        logger.info("[%s] Ambiguous message — calling LLM classifier", body.conversation_id)
        intent = await composer.classify_intent(body.message)

    # Step 3: Check if we should exit based on conversation state
    # Get merchant context first (needed for subscription tier check)
    merchant = context_store.get("merchant", body.merchant_id or "")

    # Fallback: if merchant_id doesn't match, try to find any merchant
    if not merchant:
        all_merchants = context_store.get_all("merchant")
        if all_merchants:
            merchant = next(iter(all_merchants.values()))

    if conv.should_exit(merchant=merchant):
        conv.ended = True
        logger.info(
            "[%s] Conversation exit threshold reached — ending",
            body.conversation_id,
        )
        return {
            "action": "end",
            "rationale": "Too many unanswered turns — gracefully exiting.",
        }

    category = None
    if merchant:
        category = context_store.get(
            "category", merchant.get("category_slug", "")
        )

    reply_data = await composer.compose_reply(
        merchant=merchant,
        category=category,
        conversation_history=conv.get_history_text(),
        merchant_message=body.message,
        intent=intent,
        detected_language=conv.detected_language,
    )

    if not reply_data:
        return {
            "action": "end",
            "rationale": "Failed to compose reply — ending conversation.",
        }

    # Safety net: if LLM returned "end" on an intent_action, override with
    # a direct action-oriented response — the merchant said YES.
    if intent == "intent_action" and reply_data.get("action") == "end":
        m_name = ""
        if merchant:
            m_name = merchant.get("identity", {}).get("owner_first_name", "")
        reply_data = {
            "action": "send",
            "body": (
                f"Done{', ' + m_name if m_name else ''}! "
                "Proceeding now — I'll draft it and send you a preview "
                "within the next few minutes. Confirm once you're happy with it."
            ),
            "cta": "binary_yes_stop",
            "rationale": "Merchant confirmed intent — switching to action mode.",
        }

    # Guard: never send the same body twice in this conversation
    if reply_data.get("action") == "send" and reply_data.get("body"):
        if conv.is_verbatim_repeat(reply_data["body"]):
            logger.warning("[%s] Verbatim repeat in reply — ending", body.conversation_id)
            conv.ended = True
            return {"action": "end", "rationale": "Avoiding verbatim repeat — ending conversation."}
        conv.add_bot_turn(reply_data["body"])

    if reply_data.get("action") == "end":
        conv.ended = True

    return reply_data


# ---------------------------------------------------------------------------
# POST /v1/teardown (optional — wipes state)
# ---------------------------------------------------------------------------


@app.post("/v1/teardown")
async def teardown() -> dict:
    context_store.clear()
    conversation_registry.clear()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Run with: uvicorn bot:app --host 0.0.0.0 --port 8080
# ---------------------------------------------------------------------------

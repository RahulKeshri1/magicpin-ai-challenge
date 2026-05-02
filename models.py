"""
Pydantic request/response models for all 5 API endpoints.

Matches the schemas defined in challenge-testing-brief.md §2-3.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ============================================================================
# POST /v1/context
# ============================================================================


class ContextPushRequest(BaseModel):
    scope: str  # "category" | "merchant" | "customer" | "trigger"
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class ContextPushResponse(BaseModel):
    accepted: bool
    ack_id: str | None = None
    stored_at: str | None = None
    reason: str | None = None
    current_version: int | None = None
    details: str | None = None


# ============================================================================
# POST /v1/tick
# ============================================================================


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = Field(default_factory=list)


class TickAction(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: str | None = None
    send_as: str  # "vera" | "merchant_on_behalf"
    trigger_id: str
    template_name: str
    template_params: list[str] = Field(default_factory=list)
    body: str
    cta: str  # "open_ended" | "binary_yes_stop" | "none"
    suppression_key: str
    rationale: str


class TickResponse(BaseModel):
    actions: list[TickAction] = Field(default_factory=list)


# ============================================================================
# POST /v1/reply
# ============================================================================


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str  # "merchant" | "customer"
    message: str
    received_at: str
    turn_number: int


class ReplyResponse(BaseModel):
    action: str  # "send" | "wait" | "end"
    body: str | None = None
    cta: str | None = None
    rationale: str | None = None
    wait_seconds: int | None = None


# ============================================================================
# GET /v1/healthz
# ============================================================================


class HealthzResponse(BaseModel):
    status: str
    uptime_seconds: int
    contexts_loaded: dict[str, int]


# ============================================================================
# GET /v1/metadata
# ============================================================================


class MetadataResponse(BaseModel):
    team_name: str
    team_members: list[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str


# ============================================================================
# Internal: composed message from the composer engine
# ============================================================================


class ComposedMessage(BaseModel):
    """Output from the EngagementComposer — used internally, not sent over API."""

    body: str
    cta: str = "open_ended"
    send_as: str = "vera"
    suppression_key: str = ""
    rationale: str = ""
    template_name: str = "vera_generic_v1"
    template_params: list[str] = Field(default_factory=list)

"""
Versioned in-memory context store.

Stores all context pushes from the judge (categories, merchants, customers,
triggers) keyed by (scope, context_id) with version tracking for idempotent
upserts.

Thread-safe for the single-process async FastAPI model.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("context")

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextStore:
    """Singleton context store — holds all pushed contexts in memory."""

    def __init__(self) -> None:
        # (scope, context_id) -> {"version": int, "payload": dict}
        self._store: dict[tuple[str, str], dict[str, Any]] = {}
        # Track suppression keys already sent this session
        self._sent_suppression_keys: set[str] = set()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def upsert(
        self, scope: str, context_id: str, version: int, payload: dict
    ) -> tuple[bool, int | None]:
        """Insert or update a context entry.

        Returns:
            (True, None)              — accepted
            (False, current_version)  — rejected (stale version)
        """
        key = (scope, context_id)
        existing = self._store.get(key)

        if existing and existing["version"] >= version:
            return False, existing["version"]

        self._store[key] = {"version": version, "payload": payload}
        logger.debug(
            "Stored %s/%s v%d (%d bytes)",
            scope,
            context_id,
            version,
            len(str(payload)),
        )
        return True, None

    def get(self, scope: str, context_id: str) -> dict | None:
        """Return the payload for a (scope, context_id), or None."""
        entry = self._store.get((scope, context_id))
        return entry["payload"] if entry else None

    def get_all(self, scope: str) -> dict[str, dict]:
        """Return all payloads for a given scope as {context_id: payload}."""
        return {
            cid: entry["payload"]
            for (s, cid), entry in self._store.items()
            if s == scope
        }

    def count(self, scope: str) -> int:
        """Count how many entries exist for a scope."""
        return sum(1 for (s, _) in self._store if s == scope)

    def counts(self) -> dict[str, int]:
        """Return counts for all scopes."""
        result: dict[str, int] = {}
        for (s, _) in self._store:
            result[s] = result.get(s, 0) + 1
        return result

    # ------------------------------------------------------------------
    # Resolution helpers (trigger → merchant → category → customer)
    # ------------------------------------------------------------------

    def resolve_trigger(
        self, trigger_id: str
    ) -> tuple[dict | None, dict | None, dict | None, dict | None]:
        """Given a trigger_id, resolve the full context chain.

        Returns:
            (trigger, merchant, category, customer) — any may be None if
            the referenced ID isn't stored yet.
        """
        trigger = self.get("trigger", trigger_id)
        if not trigger:
            return None, None, None, None

        merchant_id = trigger.get("merchant_id")
        merchant = self.get("merchant", merchant_id) if merchant_id else None

        category_slug = None
        if merchant:
            category_slug = merchant.get("category_slug")
        elif trigger.get("payload", {}).get("category"):
            category_slug = trigger["payload"]["category"]

        category = self.get("category", category_slug) if category_slug else None

        customer_id = trigger.get("customer_id")
        customer = self.get("customer", customer_id) if customer_id else None

        return trigger, merchant, category, customer

    # ------------------------------------------------------------------
    # Suppression tracking
    # ------------------------------------------------------------------

    def is_suppressed(self, suppression_key: str) -> bool:
        """Check if a suppression key has already been sent."""
        return suppression_key in self._sent_suppression_keys

    def mark_sent(self, suppression_key: str) -> None:
        """Mark a suppression key as sent."""
        if suppression_key:
            self._sent_suppression_keys.add(suppression_key)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Wipe all state (used on teardown)."""
        self._store.clear()
        self._sent_suppression_keys.clear()
        logger.info("Context store cleared.")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
context_store = ContextStore()

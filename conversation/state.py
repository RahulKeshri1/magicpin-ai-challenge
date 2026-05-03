"""
Per-conversation state tracking and auto-reply / intent detection.

Each conversation (identified by conversation_id) tracks:
- The turns exchanged so far
- The merchant & trigger context it was started from
- Auto-reply detection state
- Whether the conversation has ended
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("conversation")


def detect_language(text: str) -> str:
    """Detect primary script: 'hi' for Devanagari-heavy text, 'en' otherwise."""
    if not text:
        return "en"
    devanagari = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    if devanagari > max(1, len(text) * 0.08):
        return "hi"
    return "en"


# Known auto-reply phrases (lowercased substrings)
AUTO_REPLY_PATTERNS: list[str] = [
    "thank you for contacting",
    "our team will respond",
    "automated assistant",
    "auto-generated message",
    "we will get back to you",
    "will revert back",
    "will get back to you",
    "please leave your message",
    "currently unavailable",
    "i am an automated",
    "aapki jaankari ke liye",
    "hamari team tak pahuncha",
    "yeh sabhi baatein",
]

# Phrases that signal explicit intent to proceed
INTENT_ACTION_PHRASES: list[str] = [
    "let's do it",
    "lets do it",
    "go ahead",
    "yes do it",
    "haan kar do",
    "haan karo",
    "ok proceed",
    "proceed",
    "yes please",
    "ha kar do",
    "ok done",
    "chalo karte hain",
    "kar dijiye",
    "yes i want",
    "mujhe join karna hai",
    "mujhe judrna hai",
    "i want to join",
    "sign me up",
    "start karo",
    "let's start",
    "ok let's go",
    "confirm",
    "confirm karo",
]

# Phrases that signal the merchant is not interested
NOT_INTERESTED_PHRASES: list[str] = [
    "not interested",
    "stop messaging",
    "don't contact",
    "stop",
    "unsubscribe",
    "leave me alone",
    "spam",
    "useless",
    "band karo",
    "mat bhejo",
    "nahi chahiye",
    "not needed",
    "please stop",
    "remove me",
]


@dataclass
class Turn:
    """A single turn in a conversation."""

    role: str  # "vera" | "merchant" | "customer"
    body: str
    turn_number: int
    timestamp: str = ""


@dataclass
class ConversationState:
    """Tracks the state of a single conversation."""

    conversation_id: str
    merchant_id: str
    customer_id: str | None = None
    trigger_id: str | None = None
    turns: list[Turn] = field(default_factory=list)
    ended: bool = False
    auto_reply_count: int = 0
    sent_bodies: set = field(default_factory=set)
    detected_language: str | None = None

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def add_bot_turn(self, body: str, turn_number: int = 0) -> None:
        """Record a message sent by the bot and track it for dedup."""
        tn = turn_number or self.turn_count + 1
        self.turns.append(Turn(role="vera", body=body, turn_number=tn))
        self.sent_bodies.add(body.strip())

    def add_merchant_turn(
        self, body: str, turn_number: int = 0, timestamp: str = ""
    ) -> None:
        """Record a merchant/customer reply and auto-detect language."""
        tn = turn_number or self.turn_count + 1
        self.turns.append(
            Turn(role="merchant", body=body, turn_number=tn, timestamp=timestamp)
        )
        lang = detect_language(body)
        if lang == "hi":
            self.detected_language = "hi"

    def is_verbatim_repeat(self, body: str) -> bool:
        """Return True if this exact body was already sent in this conversation."""
        return body.strip() in self.sent_bodies

    def get_history_text(self, max_turns: int = 10) -> str:
        """Format recent turns as text for prompt injection."""
        recent = self.turns[-max_turns:]
        lines: list[str] = []
        for t in recent:
            label = "VERA" if t.role == "vera" else "MERCHANT"
            lines.append(f"[{label}] {t.body}")
        return "\n".join(lines)

    def detect_auto_reply(self, message: str) -> bool:
        """Check if a message looks like a WhatsApp Business auto-reply.

        Heuristics:
        1. Contains known auto-reply phrases
        2. Same message verbatim as a previous merchant message
        """
        msg_lower = message.lower().strip()

        # Pattern 1: Known auto-reply phrases
        if any(p in msg_lower for p in AUTO_REPLY_PATTERNS):
            self.auto_reply_count += 1
            return True

        # Pattern 2: Same message repeated by merchant (exclude current turn)
        prev_merchant_msgs = [
            t.body.strip() for t in self.turns[:-1] if t.role == "merchant"
        ]
        if prev_merchant_msgs and message.strip() in prev_merchant_msgs:
            self.auto_reply_count += 1
            return True

        # Not an auto-reply — reset counter
        self.auto_reply_count = 0
        return False

    def detect_intent_action(self, message: str) -> bool:
        """Check if the merchant is signaling 'yes, do it'."""
        msg_lower = message.lower().strip()
        return any(phrase in msg_lower for phrase in INTENT_ACTION_PHRASES)

    def detect_not_interested(self, message: str) -> bool:
        """Check if the merchant wants us to stop."""
        msg_lower = message.lower().strip()
        return any(phrase in msg_lower for phrase in NOT_INTERESTED_PHRASES)

    def should_exit(self, merchant: dict | None = None) -> bool:
        """Should we end this conversation?

        Rules:
        - 2+ auto-replies in a row → exit (confirmed bot)
        - For PREMIUM merchants (Pro plan): 5+ unanswered turns → exit
        - For BASIC merchants: 3+ unanswered turns → exit
        """
        if self.auto_reply_count >= 2:
            return True

        # Determine exit threshold based on merchant subscription tier
        max_unanswered = 3  # Default for basic
        if merchant:
            subscription = merchant.get("subscription", {})
            plan = subscription.get("plan", "basic")
            if plan.lower() in ("pro", "premium", "enterprise"):
                max_unanswered = 5

        # Count consecutive unanswered bot turns at the end
        unanswered = 0
        for t in reversed(self.turns):
            if t.role == "vera":
                unanswered += 1
            else:
                break
        
        return unanswered >= max_unanswered


class ConversationRegistry:
    """Manages all active conversations."""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationState] = {}

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: str = "",
        customer_id: str | None = None,
        trigger_id: str | None = None,
    ) -> ConversationState:
        """Get existing conversation or create a new one."""
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationState(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
            )
        return self._conversations[conversation_id]

    def get(self, conversation_id: str) -> ConversationState | None:
        return self._conversations.get(conversation_id)

    def count(self) -> int:
        return len(self._conversations)

    def clear(self) -> None:
        self._conversations.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
conversation_registry = ConversationRegistry()

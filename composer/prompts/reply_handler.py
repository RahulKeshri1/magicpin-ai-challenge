"""
Prompt builder for multi-turn reply handling.
"""

from __future__ import annotations
import json


def reply_prompt(
    merchant: dict,
    category: dict | None,
    conversation_history: str,
    merchant_message: str,
    intent: str,
) -> str:
    m_id = merchant.get("identity", {})
    offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    return f"""\
═══ MERCHANT ═══
Name: {m_id.get('name', '')} | Owner: {m_id.get('owner_first_name', '')}
Languages: {', '.join(m_id.get('languages', ['en']))}
Active offers: {', '.join(offers) if offers else 'None'}
Signals: {', '.join(merchant.get('signals', []))}

═══ CONVERSATION SO FAR ═══
{conversation_history}

═══ LATEST MERCHANT MESSAGE ═══
"{merchant_message}"

═══ DETECTED INTENT ═══
{intent}

═══ INSTRUCTIONS ═══
Continue the conversation based on the detected intent.

If intent is "intent_action":
• The merchant said YES. Switch to ACTION mode immediately.
• Do NOT ask another qualifying question. Start doing the thing.
• Tell them what you're doing right now, and what comes next.

If intent is "intent_question":
• Answer their question using the merchant context.
• Then advance the conversation toward the next step.

If intent is "engaged":
• The merchant is engaged. Continue the conversation naturally.
• Offer the next logical step.

If intent is "auto_reply":
• This is a canned WhatsApp Business auto-reply.
• Try ONE gentle re-engagement ("quick question" or "2-min check").
• If this is the 2nd+ auto-reply, return action="end" instead.

If intent is "not_interested":
• Respect their decision. Exit politely. No more than 1 sentence.
• Return action="end".

Return JSON:
{{
  "action": "send" | "wait" | "end",
  "body": "<your reply — only if action=send>",
  "cta": "open_ended | binary_yes_stop | none",
  "rationale": "<why this response>"
}}"""

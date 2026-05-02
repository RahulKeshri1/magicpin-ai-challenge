"""
Main composition engine — the brain of the bot.

Takes the 4 context layers, builds a prompt, calls the LLM, parses the JSON
output, validates it, and returns a ComposedMessage.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from composer.prompts.base_system import build_system_prompt
from composer.prompts.reply_handler import reply_prompt
from composer.router import get_prompt_builder
from composer.validator import validator
from llm.client import llm_router, LLMTimeoutError, LLMRateLimitError
from models import ComposedMessage

logger = logging.getLogger("composer")


class EngagementComposer:
    """Composes messages from the 4 context layers using the LLM."""

    async def compose(
        self,
        category: dict | None,
        merchant: dict | None,
        trigger: dict,
        customer: dict | None = None,
    ) -> ComposedMessage | None:
        """Compose a proactive message for a trigger.

        Returns ComposedMessage or None if composition fails.
        On LLM failure, attempts fallback template before giving up.
        """
        if not merchant or not trigger:
            logger.warning("Missing merchant or trigger context — skipping")
            return None

        kind = trigger.get("kind", "unknown")
        scope = trigger.get("scope", "merchant")

        # Customer-scoped triggers require a customer context
        if scope == "customer" and not customer:
            logger.debug("Skipping customer trigger %s — no customer context", kind)
            return None

        # Get the right prompt builder
        prompt_builder = get_prompt_builder(kind, scope)

        # Build the user prompt
        try:
            if scope == "customer" and customer:
                user_prompt = prompt_builder(
                    category or {}, merchant, trigger, customer
                )
            else:
                user_prompt = prompt_builder(
                    category or {}, merchant, trigger
                )
        except Exception as e:
            logger.error("Prompt builder failed for kind=%s: %s", kind, e)
            return self._fallback_message(merchant, trigger, scope)

        # Build system prompt with category voice
        system_prompt = build_system_prompt(category)

        # Call the LLM
        try:
            raw_response = await llm_router.compose(
                prompt=user_prompt, system=system_prompt
            )
        except (LLMTimeoutError, LLMRateLimitError) as e:
            logger.error("LLM call failed: %s — using fallback", e)
            return self._fallback_message(merchant, trigger, scope)

        # Parse the JSON response
        composed = self._parse_response(raw_response, trigger)

        # Post-LLM validation — retry once on critical issues
        if composed and composed.body:
            composed = await self._validate_with_retry(
                composed=composed,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                category=category,
                merchant=merchant,
                trigger=trigger,
                customer=customer,
            )

        return composed

    async def compose_reply(
        self,
        merchant: dict | None,
        category: dict | None,
        conversation_history: str,
        merchant_message: str,
        intent: str,
    ) -> dict | None:
        """Compose a reply to a merchant message in a multi-turn conversation.

        Returns a dict with {action, body, cta, rationale} or None.
        """
        if not merchant:
            return {"action": "end", "rationale": "No merchant context available"}

        user_prompt = reply_prompt(
            merchant=merchant,
            category=category,
            conversation_history=conversation_history,
            merchant_message=merchant_message,
            intent=intent,
        )

        system_prompt = build_system_prompt(category)

        try:
            raw_response = await llm_router.compose(
                prompt=user_prompt, system=system_prompt
            )
        except (LLMTimeoutError, LLMRateLimitError) as e:
            logger.error("Reply LLM call failed: %s", e)
            return {"action": "end", "rationale": f"LLM error: {e}"}

        return self._parse_reply_response(raw_response)

    async def classify_intent(self, message: str) -> str:
        """Classify a merchant message into an intent category.

        Uses the lightweight Groq classifier.
        Returns one of: auto_reply, intent_action, intent_question,
                         not_interested, engaged
        """
        system = (
            "You are an intent classifier for WhatsApp merchant messages. "
            "Output EXACTLY one label from the list, nothing else — no punctuation, "
            "no explanation, no extra words."
        )
        prompt = f"""\
Classify this merchant WhatsApp message into exactly one of these labels:
  auto_reply      — automated/canned WhatsApp Business response
  intent_action   — merchant wants to proceed ("yes", "go ahead", "let's do it", "haan kar do")
  intent_question — merchant is asking a question
  not_interested  — merchant wants to stop or unsubscribe
  engaged         — merchant is engaged but none of the above apply

Message: "{message}"

Output only the label."""

        valid = {
            "auto_reply", "intent_action", "intent_question",
            "not_interested", "engaged",
        }
        try:
            result = await llm_router.classify(prompt=prompt, system=system)
            # Strip whitespace, quotes, punctuation
            label = result.strip().lower().strip('"\'.,!?')
            # If the model returned a sentence, extract the last word (the label)
            if label not in valid:
                for token in reversed(label.split()):
                    cleaned = token.strip('"\'.,!?')
                    if cleaned in valid:
                        label = cleaned
                        break
            return label if label in valid else "engaged"
        except Exception as e:
            logger.warning("Intent classification failed: %s", e)
            return "engaged"

    # ------------------------------------------------------------------
    # Validation + conditional retry
    # ------------------------------------------------------------------

    async def _validate_with_retry(
        self,
        composed: "ComposedMessage",
        user_prompt: str,
        system_prompt: str,
        category: dict | None,
        merchant: dict | None,
        trigger: dict,
        customer: dict | None,
    ) -> "ComposedMessage":
        """Run validation; on critical failures ask the LLM to fix itself (once)."""
        CRITICAL_ISSUES = {"body_too_short", "fabricated_numbers"}

        repaired_body, issues = validator.validate_and_repair(
            body=composed.body,
            category=category,
            merchant=merchant,
            trigger=trigger,
            customer=customer,
        )
        composed.body = repaired_body
        composed.rationale = validator.validate_rationale(composed.rationale, composed.body)

        critical = [i for i in issues if any(i.startswith(c) for c in CRITICAL_ISSUES)]
        if not critical:
            return composed

        # Build a focused repair prompt so the LLM fixes the specific problem
        fix_instructions = []
        if any(i == "body_too_short" for i in critical):
            fix_instructions.append(
                "The message is too short (under 150 characters). "
                "Expand it to 150-350 characters by adding specific numbers, "
                "a peer comparison, and a clear single CTA in the last sentence."
            )
        fab_issues = [i for i in critical if i.startswith("fabricated_numbers")]
        if fab_issues:
            bad_nums = fab_issues[0].split(":", 1)[-1]
            fix_instructions.append(
                f"The numbers {bad_nums} do not appear in the context — remove or replace them "
                "with real numbers from the merchant/trigger data provided."
            )

        repair_prompt = (
            f"The following message failed quality checks:\n\n"
            f"ORIGINAL: {composed.body}\n\n"
            f"ISSUES:\n" + "\n".join(f"• {f}" for f in fix_instructions) +
            f"\n\nOriginal context:\n{user_prompt}\n\n"
            "Rewrite the message fixing only the issues above. "
            "Return the same JSON format: {\"body\": \"...\", \"cta\": \"...\", "
            "\"send_as\": \"...\", \"suppression_key\": \"...\", \"rationale\": \"...\"}"
        )

        try:
            raw_retry = await llm_router.compose(prompt=repair_prompt, system=system_prompt)
            retried = self._parse_response(raw_retry, trigger)
            if retried and retried.body:
                _, retry_issues = validator.validate_and_repair(
                    body=retried.body,
                    category=category,
                    merchant=merchant,
                    trigger=trigger,
                    customer=customer,
                )
                retry_critical = [
                    i for i in retry_issues
                    if any(i.startswith(c) for c in CRITICAL_ISSUES)
                ]
                if not retry_critical:
                    logger.info(
                        "Retry fixed critical issues %s → clean output", critical
                    )
                    retried.rationale = validator.validate_rationale(
                        retried.rationale, retried.body
                    )
                    return retried
                logger.warning(
                    "Retry did not fix all issues (remaining: %s) — using original",
                    retry_critical,
                )
        except Exception as e:
            logger.warning("Validation retry LLM call failed: %s", e)

        return composed

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _fallback_message(
        self, merchant: dict, trigger: dict, scope: str
    ) -> ComposedMessage:
        """Generate a fallback message when LLM fails.
        
        Uses template-based approach with merchant name + trigger kind.
        Better than crashing or returning None.
        """
        identity = merchant.get("identity", {})
        owner = identity.get("owner_first_name", identity.get("name", "Owner"))
        kind = trigger.get("kind", "generic")
        
        # Template by trigger kind
        templates = {
            "research_digest": f"Hi {owner}, a new research finding landed relevant to your business. Want me to share the details and suggest how to apply it?",
            "perf_dip": f"{owner}, I noticed a performance dip this week. Want me to diagnose what happened and suggest fixes?",
            "perf_spike": f"Great news {owner}! Your performance spiked this week. Want me to help you keep this momentum?",
            "festival_upcoming": f"{owner}, an important event is coming up. Want me to suggest a campaign tailored to your business?",
            "renewal_due": f"Hi {owner}, your subscription renewal is coming up soon. Let's talk about keeping your visibility strong.",
            "dormant_with_vera": f"Hi {owner}, I have something valuable for you. Want to chat briefly?",
            "review_theme_emerged": f"{owner}, your customers are mentioning something consistently. Want me to show you what?",
            "competitor_opened": f"{owner}, something is happening in your area. Interested in staying ahead?",
            "curious_ask_due": f"Quick question for you {owner} — what's your biggest win this week?",
        }
        
        body = templates.get(kind, f"Hi {owner}, let's chat about how to grow your business on magicpin.")
        
        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as="vera" if scope != "customer" else "merchant_on_behalf",
            suppression_key=trigger.get("suppression_key", ""),
            rationale=f"Fallback template (LLM failed): {kind}",
        )

    def _parse_response(
        self, raw: str, trigger: dict
    ) -> ComposedMessage | None:
        """Parse the LLM's JSON response into a ComposedMessage."""
        data = self._extract_json(raw)
        if not data:
            # Fallback: treat the entire response as the body
            logger.warning("Could not parse JSON from LLM response, using raw text")
            return ComposedMessage(
                body=raw.strip()[:500],
                cta="open_ended",
                send_as="vera" if trigger.get("scope") != "customer" else "merchant_on_behalf",
                suppression_key=trigger.get("suppression_key", ""),
                rationale="Fallback: LLM returned non-JSON",
            )

        return ComposedMessage(
            body=data.get("body", "").strip(),
            cta=data.get("cta", "open_ended"),
            send_as=data.get("send_as", "vera"),
            suppression_key=data.get(
                "suppression_key", trigger.get("suppression_key", "")
            ),
            rationale=data.get("rationale", ""),
        )

    def _parse_reply_response(self, raw: str) -> dict:
        """Parse the reply LLM response."""
        data = self._extract_json(raw)
        if not data:
            return {
                "action": "send",
                "body": raw.strip()[:500],
                "cta": "open_ended",
                "rationale": "Fallback: non-JSON response",
            }

        action = data.get("action", "send")
        if action not in ("send", "wait", "end"):
            action = "send"

        result: dict = {"action": action, "rationale": data.get("rationale", "")}
        if action == "send":
            result["body"] = data.get("body", "").strip()
            result["cta"] = data.get("cta", "open_ended")
        elif action == "wait":
            result["wait_seconds"] = data.get("wait_seconds", 1800)
        return result

    def _extract_json(self, text: str) -> dict | None:
        """Extract a JSON object from LLM output.

        Handles: markdown fences, thinking blocks, extra text before/after.
        """
        if not text:
            return None

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = cleaned.strip()

        # Try direct parse on cleaned text
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # Find all JSON-like blocks using brace matching
        # This handles cases where the LLM prepends thinking text
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        start = -1

        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
composer = EngagementComposer()

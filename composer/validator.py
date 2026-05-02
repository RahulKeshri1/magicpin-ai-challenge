"""
Post-LLM validation and auto-repair for composed messages.

Runs after the LLM returns a ComposedMessage and fixes common issues
that would cost points on the 5-dimension rubric:

  1. Taboo word removal
  2. Body length enforcement (150-500 chars)
  3. CTA position check (must be last sentence)
  4. Merchant name/owner usage verification
  5. Language preference check
  6. Fabrication detection (numbers must exist in context)
  7. No self-introduction after turn 1
  8. No generic percentage-off offers
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("validator")


class CompositionValidator:
    """Validates and auto-repairs composed messages before sending."""

    def validate_and_repair(
        self,
        body: str,
        category: dict | None,
        merchant: dict | None,
        trigger: dict | None,
        customer: dict | None = None,
        is_reply: bool = False,
    ) -> tuple[str, list[str]]:
        """Validate and repair a composed message body.

        Returns:
            (repaired_body, list_of_issues_found)
        """
        issues: list[str] = []
        repaired = body

        if not repaired:
            return repaired, ["empty_body"]

        # 1. Taboo word check
        repaired, taboo_issues = self._check_taboo_words(repaired, category)
        issues.extend(taboo_issues)

        # 2. Body length
        repaired, length_issues = self._check_body_length(repaired)
        issues.extend(length_issues)

        # 3. Generic offer patterns
        repaired, offer_issues = self._check_generic_offers(repaired)
        issues.extend(offer_issues)

        # 4. Merchant name usage
        if merchant and not is_reply:
            name_issues = self._check_merchant_name(repaired, merchant)
            issues.extend(name_issues)

        # 5. Multiple CTAs
        cta_issues = self._check_multiple_ctas(repaired)
        issues.extend(cta_issues)

        # 6. Preamble check
        preamble_issues = self._check_preamble(repaired)
        issues.extend(preamble_issues)

        # 7. Self-introduction on reply
        if is_reply:
            intro_issues = self._check_self_intro(repaired)
            issues.extend(intro_issues)

        # 8. Fabrication detection — numbers must exist in context
        fab_issues = self._check_fabrication(
            repaired, category, merchant, trigger, customer
        )
        issues.extend(fab_issues)

        # 9. CTA position check — must be in last sentence
        cta_pos_issues = self._check_cta_position(repaired)
        issues.extend(cta_pos_issues)

        if issues:
            logger.info("Validator found %d issue(s): %s", len(issues), issues)

        return repaired, issues

    def validate_rationale(self, rationale: str, body: str) -> str:
        """Ensure the rationale is concise and relates to the body."""
        if not rationale:
            return "Message composed from trigger context with merchant-specific data."

        # Truncate overly long rationales
        if len(rationale) > 300:
            rationale = rationale[:297] + "..."

        return rationale

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_taboo_words(
        self, body: str, category: dict | None
    ) -> tuple[str, list[str]]:
        """Check for taboo vocabulary from the category voice.

        Drops the entire sentence containing a taboo word rather than
        surgically removing the word, which prevents broken grammar.
        Falls back to word-level removal only if dropping the sentence
        would leave the body empty.
        """
        issues: list[str] = []
        if not category:
            return body, issues

        taboo = category.get("voice", {}).get("vocab_taboo", [])
        body_lower = body.lower()

        for word in taboo:
            clean_word = word.split("(")[0].strip().lower()
            if not clean_word or clean_word not in body_lower:
                continue

            issues.append(f"taboo_word:{clean_word}")

            # Split into sentences and drop any that contain the taboo word
            sentences = re.split(r"(?<=[.!?।])\s+", body)
            filtered = [
                s for s in sentences
                if clean_word not in s.lower()
            ]

            if filtered:
                body = " ".join(filtered).strip()
            else:
                # Dropping the sentence would leave nothing — fall back to word removal
                body = re.sub(re.escape(clean_word), "", body, flags=re.IGNORECASE)
                body = re.sub(r"  +", " ", body).strip()

            body_lower = body.lower()

        return body, issues

    def _check_body_length(self, body: str) -> tuple[str, list[str]]:
        """Ensure body is between 150-500 characters (rubric minimum is 150)."""
        issues: list[str] = []

        if len(body) > 500:
            # Truncate at the last sentence boundary before 500
            truncated = body[:500]
            last_period = max(
                truncated.rfind("."),
                truncated.rfind("?"),
                truncated.rfind("!"),
                truncated.rfind("।"),  # Hindi purna viram
            )
            if last_period > 200:
                body = truncated[: last_period + 1]
            else:
                body = truncated
            issues.append("body_too_long_truncated")

        if len(body) < 150:
            issues.append("body_too_short")

        return body, issues

    def _check_generic_offers(self, body: str) -> tuple[str, list[str]]:
        """Check for generic "X% off" patterns that should be service+price."""
        issues: list[str] = []
        # Flag "flat X% off" or "X% discount" without service name
        pattern = r"(?:flat\s+)?\d{1,2}%\s+(?:off|discount)"
        if re.search(pattern, body, re.IGNORECASE):
            issues.append("generic_percentage_offer")
        return body, issues

    def _check_merchant_name(
        self, body: str, merchant: dict
    ) -> list[str]:
        """Check that merchant's owner name or business name is used."""
        issues: list[str] = []
        identity = merchant.get("identity", {})

        owner = identity.get("owner_first_name", "")
        name = identity.get("name", "")

        if owner and owner.lower() not in body.lower():
            if name and name.lower() not in body.lower():
                issues.append("missing_merchant_name")

        return issues

    def _check_multiple_ctas(self, body: str) -> list[str]:
        """Check for multiple asks/CTAs in the message."""
        issues: list[str] = []
        cta_patterns = [
            r"want me to",
            r"shall i",
            r"should i",
            r"would you like",
            r"reply\s+(?:yes|1|2|confirm)",
            r"kya aap chahte",
            r"batayein\?",
        ]
        cta_count = sum(
            1
            for p in cta_patterns
            if re.search(p, body, re.IGNORECASE)
        )
        if cta_count > 1:
            issues.append("multiple_ctas")
        return issues

    def _check_preamble(self, body: str) -> list[str]:
        """Check for useless preambles."""
        issues: list[str] = []
        preambles = [
            "i hope you're doing well",
            "i hope this finds you",
            "hope you're having a",
            "good morning",
            "good afternoon",
            "good evening",
            "namaste, i am vera",
            "hello, this is vera",
            "greetings from",
        ]
        body_lower = body.lower()[:100]
        for p in preambles:
            if p in body_lower:
                issues.append("useless_preamble")
                break
        return issues

    def _check_self_intro(self, body: str) -> list[str]:
        """Check for self-introduction in reply messages (not first turn)."""
        issues: list[str] = []
        intros = [
            "i am vera",
            "this is vera",
            "my name is vera",
            "i'm vera",
            "main vera",
            "vera here",
        ]
        body_lower = body.lower()
        for intro in intros:
            if intro in body_lower:
                issues.append("self_intro_in_reply")
                break
        return issues

    def _check_fabrication(
        self,
        body: str,
        category: dict | None,
        merchant: dict | None,
        trigger: dict | None,
        customer: dict | None,
    ) -> list[str]:
        """Check that all numbers in body exist in context (fabrication detection).

        Extracts all numbers from body and from contexts. Flags any number
        in body that doesn't appear in any context.
        """
        issues: list[str] = []

        # Extract numbers from body
        body_numbers = set(re.findall(r"\d+(?:\.\d+)?", body))
        if not body_numbers:
            return issues

        # Extract numbers from contexts
        context_numbers = extract_context_numbers(
            category, merchant, trigger, customer
        )

        # Check each number in body against context
        fabricated = []
        for num in body_numbers:
            if num not in context_numbers:
                # Also check percentage forms (e.g., "38" vs "0.38")
                as_percent = str(float(num) / 100) if "." not in num else str(float(num) * 100)
                as_percent_int = as_percent.split(".")[0]
                
                if not any(
                    num == cn
                    or as_percent == cn
                    or as_percent_int == cn
                    or cn.startswith(num)  # "2410" matches "2410.5"
                    for cn in context_numbers
                ):
                    fabricated.append(num)

        if fabricated:
            issues.append(f"fabricated_numbers:{','.join(fabricated)}")
            logger.warning(
                "Fabrication detected in body: numbers %s not in context",
                fabricated,
            )

        return issues

    def _check_cta_position(self, body: str) -> list[str]:
        """Check that CTA is in the last sentence."""
        issues: list[str] = []

        # Split into sentences
        sentences = re.split(r'[.!?।]', body)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) < 2:
            return issues

        # CTA patterns
        cta_patterns = [
            r"want\s+(?:me\s+)?to",
            r"shall\s+i",
            r"should\s+i",
            r"would\s+you\s+like",
            r"reply\s+(?:yes|1|2|confirm)",
            r"\?\s*$",  # Question mark at end
        ]

        # Check if CTA exists in non-final sentences
        for i, sent in enumerate(sentences[:-1]):  # Check all but last
            if any(re.search(p, sent, re.IGNORECASE) for p in cta_patterns):
                issues.append("cta_not_in_final_sentence")
                break

        return issues


# ---------------------------------------------------------------------------
# Utility: extract all numbers from context for fabrication check
# ---------------------------------------------------------------------------


def extract_context_numbers(
    category: dict | None,
    merchant: dict | None,
    trigger: dict | None,
    customer: dict | None = None,
) -> set[str]:
    """Extract all numeric values from the 4 context dicts.

    Used for fabrication detection — any number in the message body
    should exist somewhere in the contexts.
    """
    numbers: set[str] = set()

    def _extract_from(obj: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(obj, dict):
            for v in obj.values():
                _extract_from(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _extract_from(item, depth + 1)
        elif isinstance(obj, (int, float)):
            # Store as string, handle common formats
            numbers.add(str(obj))
            if isinstance(obj, float):
                numbers.add(f"{obj:.0f}")
                numbers.add(f"{obj:.1f}")
                numbers.add(f"{obj:.2f}")
                # Percentage form
                if 0 < obj < 1:
                    numbers.add(f"{obj * 100:.0f}")
        elif isinstance(obj, str):
            # Extract embedded numbers
            for m in re.findall(r"\d+(?:\.\d+)?", str(obj)):
                numbers.add(m)

    for ctx in [category, merchant, trigger, customer]:
        if ctx:
            _extract_from(ctx)

    return numbers


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
validator = CompositionValidator()

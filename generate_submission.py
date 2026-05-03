#!/usr/bin/env python3
"""
Generate submission.jsonl — the 30 canonical test-pair compositions.

Usage:
    python generate_submission.py

Reads the expanded dataset from dataset/expanded/ and runs the composer
against each of the 30 (merchant, trigger) pairs in test_pairs.json.
Writes one JSON line per pair to submission.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("generate_submission")

DATASET_DIR = Path(__file__).parent / "dataset" / "expanded"
OUTPUT_FILE = Path(__file__).parent / "submission.jsonl"


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_dataset() -> tuple[dict, dict, dict, dict]:
    """Load all expanded context files into dicts keyed by ID."""
    categories: dict[str, dict] = {}
    for f in (DATASET_DIR / "categories").glob("*.json"):
        data = load_json(f)
        categories[data["slug"]] = data

    merchants: dict[str, dict] = {}
    for f in (DATASET_DIR / "merchants").glob("*.json"):
        data = load_json(f)
        merchants[data["merchant_id"]] = data

    customers: dict[str, dict] = {}
    for f in (DATASET_DIR / "customers").glob("*.json"):
        data = load_json(f)
        customers[data["customer_id"]] = data

    triggers: dict[str, dict] = {}
    for f in (DATASET_DIR / "triggers").glob("*.json"):
        data = load_json(f)
        triggers[data["id"]] = data

    logger.info(
        "Loaded %d categories, %d merchants, %d customers, %d triggers",
        len(categories), len(merchants), len(customers), len(triggers),
    )
    return categories, merchants, customers, triggers


async def compose_pair(
    test_id: str,
    trigger_id: str,
    merchant_id: str,
    customer_id: str | None,
    categories: dict,
    merchants: dict,
    customers: dict,
    triggers: dict,
) -> dict | None:
    from composer.engine import composer

    trigger = triggers.get(trigger_id)
    merchant = merchants.get(merchant_id)
    if not trigger or not merchant:
        logger.warning("Missing context for %s (trigger=%s, merchant=%s)", test_id, trigger_id, merchant_id)
        return None

    category_slug = merchant.get("category_slug", "")
    category = categories.get(category_slug)
    customer = customers.get(customer_id) if customer_id else None

    logger.info("Composing %s: %s / %s", test_id, merchant_id, trigger_id)

    try:
        composed = await composer.compose(
            category=category,
            merchant=merchant,
            trigger=trigger,
            customer=customer,
        )
    except Exception as e:
        logger.error("Composition failed for %s: %s", test_id, e)
        return None

    if not composed or not composed.body:
        logger.warning("Empty composition for %s", test_id)
        return None

    return {
        "test_id": test_id,
        "body": composed.body,
        "cta": composed.cta,
        "send_as": composed.send_as,
        "suppression_key": composed.suppression_key,
        "rationale": composed.rationale,
    }


FALLBACK_SIGNALS = [
    "let's chat about how to grow",
    "Fallback template",
    "I noticed a performance dip this week. Want me to diagnose",
    "Your performance spiked this week. Want me to help you keep",
]


def is_fallback_body(body: str) -> bool:
    return any(sig in body for sig in FALLBACK_SIGNALS)


async def main() -> None:
    test_pairs_path = DATASET_DIR / "test_pairs.json"
    if not test_pairs_path.exists():
        logger.error("test_pairs.json not found at %s", test_pairs_path)
        sys.exit(1)

    pairs = load_json(test_pairs_path)["pairs"]
    logger.info("Found %d test pairs", len(pairs))

    # Load any already-good entries so reruns don't redo everything
    existing: dict[str, dict] = {}
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if not is_fallback_body(entry.get("body", "")):
                        existing[entry["test_id"]] = entry
        logger.info(
            "Loaded %d existing good entries (will skip these)", len(existing)
        )

    categories, merchants, customers, triggers = load_dataset()

    results: dict[str, dict] = dict(existing)
    for pair in pairs:
        tid = pair["test_id"]
        if tid in existing:
            logger.info("  %s SKIP (already good)", tid)
            continue

        result = await compose_pair(
            test_id=tid,
            trigger_id=pair["trigger_id"],
            merchant_id=pair["merchant_id"],
            customer_id=pair.get("customer_id"),
            categories=categories,
            merchants=merchants,
            customers=customers,
            triggers=triggers,
        )
        if result and not is_fallback_body(result["body"]):
            results[tid] = result
            logger.info("  %s OK: %s", tid, result["body"][:80])
        elif result:
            results[tid] = result
            logger.warning("  %s FALLBACK: %s", tid, result["body"][:80])
        else:
            logger.warning("  %s FAILED — skipped", tid)

    # Write in canonical test_id order
    order = [p["test_id"] for p in pairs]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for tid in order:
            if tid in results:
                f.write(json.dumps(results[tid], ensure_ascii=False) + "\n")

    good = sum(1 for e in results.values() if not is_fallback_body(e.get("body", "")))
    logger.info(
        "Written %d / %d entries (%d good, %d fallback) to %s",
        len(results), len(pairs), good, len(results) - good, OUTPUT_FILE,
    )
    if good < len(pairs):
        logger.warning(
            "Re-run this script after the Gemini free-tier resets (daily quota). "
            "Good entries are preserved across reruns."
        )


if __name__ == "__main__":
    asyncio.run(main())

"""
Routes trigger.kind to the correct prompt builder function.
"""

from __future__ import annotations
from typing import Callable

from composer.prompts import merchant_facing as mf
from composer.prompts import customer_facing as cf

# Merchant-facing trigger kind → prompt builder
MERCHANT_ROUTES: dict[str, Callable] = {
    "research_digest": mf.research_digest_prompt,
    "category_research_digest_release": mf.research_digest_prompt,
    "perf_dip": mf.perf_change_prompt,
    "perf_spike": mf.perf_change_prompt,
    "milestone_reached": mf.milestone_prompt,
    "festival_upcoming": mf.festival_prompt,
    "weather_heatwave": mf.festival_prompt,
    "local_news_event": mf.festival_prompt,
    "ipl_match_today": mf.festival_prompt,
    "dormant_with_vera": mf.dormant_prompt,
    "review_theme_emerged": mf.review_theme_prompt,
    "competitor_opened": mf.competitor_prompt,
    "category_trend_movement": mf.competitor_prompt,
    "renewal_due": mf.renewal_prompt,
    "curious_ask_due": mf.curious_ask_prompt,
    "scheduled_recurring": mf.curious_ask_prompt,
}

# Customer-facing trigger kind → prompt builder
CUSTOMER_ROUTES: dict[str, Callable] = {
    "recall_due": cf.recall_due_prompt,
    "customer_lapsed_soft": cf.customer_lapsed_prompt,
    "customer_lapsed_hard": cf.customer_lapsed_prompt,
    "appointment_tomorrow": cf.appointment_tomorrow_prompt,
    "chronic_refill_due": cf.chronic_refill_prompt,
    "trial_followup": cf.trial_followup_prompt,
}


def get_prompt_builder(
    kind: str, scope: str
) -> Callable:
    """Return the prompt builder for a trigger kind + scope.

    Falls back to generic prompts if no specific handler exists.
    """
    if scope == "customer":
        return CUSTOMER_ROUTES.get(kind, cf.generic_customer_prompt)
    return MERCHANT_ROUTES.get(kind, mf.generic_merchant_prompt)

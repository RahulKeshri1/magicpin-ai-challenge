"""
User-prompt builders for merchant-facing compositions.

Each function takes the 4 context dicts and returns the user-prompt string
that, combined with the system prompt, produces the composed message.
"""

from __future__ import annotations

import json
from typing import Any


def _merchant_header(merchant: dict) -> str:
    """Common merchant identity block for all prompts."""
    identity = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    delta = perf.get("delta_7d", {})
    sub = merchant.get("subscription", {})
    offers = [
        o["title"]
        for o in merchant.get("offers", [])
        if o.get("status") == "active"
    ]

    return f"""\
═══ MERCHANT ═══
Name: {identity.get('name', 'Unknown')}
Owner: {identity.get('owner_first_name', '')}
City: {identity.get('city', '')}, Locality: {identity.get('locality', '')}
Languages: {', '.join(identity.get('languages', ['en']))}
Established: {identity.get('established_year', '')}
Subscription: {sub.get('status', '')} ({sub.get('plan', '')}) — {sub.get('days_remaining', '?')} days left
Performance (30d): views={perf.get('views', '?')}, calls={perf.get('calls', '?')}, CTR={perf.get('ctr', '?')}, directions={perf.get('directions', '?')}, leads={perf.get('leads', '?')}
7d deltas: views {delta.get('views_pct', '?')}, calls {delta.get('calls_pct', '?')}
Active offers: {', '.join(offers) if offers else 'None'}
Signals: {', '.join(merchant.get('signals', []))}
Review themes: {json.dumps(merchant.get('review_themes', []), ensure_ascii=False)[:300]}"""


def _category_stats(category: dict) -> str:
    """Category peer stats block."""
    ps = category.get("peer_stats", {})
    return f"""\
═══ CATEGORY PEER STATS ({category.get('slug', '')}) ═══
Scope: {ps.get('scope', '')}
Avg rating: {ps.get('avg_rating', '?')}, Avg reviews: {ps.get('avg_review_count', '?')}
Avg views (30d): {ps.get('avg_views_30d', '?')}, Avg CTR: {ps.get('avg_ctr', '?')}
Avg post frequency: every {ps.get('avg_post_freq_days', '?')} days
Retention (6mo): {ps.get('retention_6mo_pct', '?')}"""


def _conversation_history(merchant: dict) -> str:
    """Recent conversation history — returns empty string if none exists."""
    history = merchant.get("conversation_history", [])
    if not history:
        return ""
    lines = ["═══ CONVERSATION HISTORY ═══"]
    for turn in history[-5:]:
        sender = turn.get("from", "?").upper()
        body = turn.get("body", "")[:200]
        eng = turn.get("engagement", "")
        tag = f" ({eng})" if eng else ""
        lines.append(f"[{sender}] {body}{tag}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────
# Trigger-specific prompt builders
# ────────────────────────────────────────────────────────────────────


def research_digest_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    payload = trigger.get("payload", {})
    digest_items = category.get("digest", [])

    # Find the specific digest item referenced by the trigger
    top_item_id = payload.get("top_item_id", "")
    top_item = None
    for d in digest_items:
        if d.get("id") == top_item_id:
            top_item = d
            break
    if not top_item and digest_items:
        top_item = digest_items[0]

    digest_block = ""
    if top_item:
        digest_block = f"""\
═══ DIGEST ITEM (use this as the hook) ═══
Title: {top_item.get('title', '')}
Source: {top_item.get('source', '')}
Kind: {top_item.get('kind', '')}
Summary: {top_item.get('summary', '')}
Trial size: {top_item.get('trial_n', 'N/A')}
Patient segment: {top_item.get('patient_segment', 'N/A')}
Actionable: {top_item.get('actionable', '')}"""

    cust_agg = merchant.get("customer_aggregate", {})

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{digest_block}

═══ MERCHANT CUSTOMER AGGREGATE ═══
{json.dumps(cust_agg, ensure_ascii=False)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: research_digest (external, merchant-facing)
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ CRITICAL RULES ═══
1. First sentence MUST name the source and relevance RIGHT NOW
   • "JIDA's Oct issue landed. One item for your {cust_agg.get('dominant_segment', 'patient')} cohort —"
   • NOT: "Hi, check this out..." or "I hope you're doing well..."

2. Embed numeric evidence IMMEDIATELY after the hook
   • Use: trial_n, improvement %, source citation (e.g. "JIDA Oct 2026 p.14")
   • Every number must come from the digest item above — NEVER fabricate

3. Reference THEIR specific cohort (from customer_aggregate above)
   • Connect the finding to the merchant's actual patient/customer segment

4. Offer externalized effort in the FINAL sentence ONLY — single CTA
   • "Want me to draft a patient-ed post you can reshare on your GBP?"

5. Compulsion levers (use 2-3):
   • Specific number anchor (38% improvement, not "significantly better")
   • Social proof ("X dentists in your area already adopted this")
   • Reciprocity ("I noticed your cohort — thought you'd want this first")
   • Curiosity gap ("this might change your current protocol")

Output format: {{
  "body": "<150-350 chars: source hook → numeric evidence → cohort relevance → single CTA>",
  "cta": "open_ended",
  "send_as": "vera",
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<1-2 sentences: why this digest + their cohort = good fit now>"
}}\
"""


def milestone_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    payload = trigger.get("payload", {})

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: milestone_reached (internal, merchant-facing)
Payload: {json.dumps(payload, ensure_ascii=False)}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ INSTRUCTIONS ═══
The merchant hit a milestone. Celebrate it and suggest next steps.
• Name the specific milestone from the payload.
• Use social proof — how does this compare to peers?
• Suggest leveraging it (share on GBP, run a campaign, etc.).
• send_as = "vera"
• CTA: open_ended"""


def festival_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    payload = trigger.get("payload", {})

    seasonal = category.get("seasonal_beats", [])
    offers = [
        o["title"]
        for o in merchant.get("offers", [])
        if o.get("status") == "active"
    ]

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

═══ SEASONAL BEATS ═══
{json.dumps(seasonal, ensure_ascii=False)[:500]}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: {trigger.get('kind', 'festival_upcoming')} (external, merchant-facing)
Payload: {json.dumps(payload, ensure_ascii=False)}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ INSTRUCTIONS ═══
A festival or external event is coming. Connect it to the merchant's business.
• Name the festival/event and when it is.
• Suggest a category-specific campaign or offer. Prefer service+price over discount.
• If the merchant has active offers, suggest leveraging them.
• If seasonal beats are relevant, reference them.
• send_as = "vera"
• CTA: open_ended"""


def dormant_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    payload = trigger.get("payload", {})
    perf = merchant.get("performance", {})
    delta = perf.get("delta_7d", {})
    identity = merchant.get("identity", {})
    languages = identity.get("languages", ["en"])
    peer_stats = category.get("peer_stats", {})

    # Pick the sharpest hook from available context
    hook_hint = ""
    if delta.get("views_pct"):
        hook_hint = f"Their views changed {delta.get('views_pct')}% this week — lead with that."
    elif payload.get("new_insight"):
        hook_hint = f"New insight available: {payload.get('new_insight')}"
    elif peer_stats.get("avg_ctr"):
        hook_hint = f"Category avg CTR is {peer_stats.get('avg_ctr')} — compare to their {perf.get('ctr', '?')}."

    lang_note = "Use Hindi-English code-mix." if "hi" in languages else "Use English."

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: dormant_with_vera (internal, merchant-facing)
Days dormant: {payload.get('days_dormant', '14+')}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ CRITICAL RULES ═══
1. NEVER say "you haven't replied" or "you've been silent" — that is needy and costs -4 points
2. Lead with a FRESH DATA POINT that creates curiosity or urgency
   {hook_hint}
3. Use ONE of: curiosity gap ("want to see X?"), reciprocity ("spotted this for you"), loss framing ("this window closes Friday")
4. Keep to 2-3 sentences — no padding, no pleasantries
5. {lang_note}
6. Single CTA in last sentence only

Output format: {{
  "body": "<120-280 chars: fresh hook → compulsion lever → single CTA>",
  "cta": "open_ended",
  "send_as": "vera",
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<which lever + why this hook beats a generic check-in>"
}}\
"""


def review_theme_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    themes = merchant.get("review_themes", [])

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

═══ REVIEW THEMES ═══
{json.dumps(themes, ensure_ascii=False)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: review_theme_emerged (internal, merchant-facing)
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ INSTRUCTIONS ═══
A pattern has emerged in the merchant's reviews. Tell them about it.
• Cite the specific theme, sentiment, occurrence count, and a quote if available.
• For positive themes: suggest amplifying (GBP post, offer around it).
• For negative themes: frame constructively, offer to help (draft a response template, etc.).
• send_as = "vera"
• CTA: open_ended"""


def competitor_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    payload = trigger.get("payload", {})
    perf = merchant.get("performance", {})
    identity = merchant.get("identity", {})
    languages = identity.get("languages", ["en"])
    peer_stats = category.get("peer_stats", {})

    competitor_name = payload.get("competitor_name", "")
    distance_m = payload.get("distance_meters", "")
    comp_rating = payload.get("competitor_rating", "")
    distance_note = f"{distance_m}m from you" if distance_m else "in your area"
    name_note = f'"{competitor_name}"' if competitor_name else "a new competitor"
    lang_note = "Use Hindi-English code-mix." if "hi" in languages else "Use English."

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: competitor_opened (external, merchant-facing)
Payload: {json.dumps(payload, ensure_ascii=False)}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ CRITICAL RULES ═══
1. VOYEUR-CURIOSITY frame: "Someone {distance_note} just listed on magicpin — {comp_rating and f'rated {comp_rating}' or 'their profile looks sharp'}. Want to see how you compare?"
2. {"Do NOT name the competitor — just say 'someone new'." if not competitor_name else f"You MAY name {name_note} since it's in the payload."}
3. Suggest ONE defensive action (most impactful for their profile gap):
   • Profile photos stale? → suggest refresh
   • No active offer? → suggest creating one
   • CTR below peer ({peer_stats.get('avg_ctr', '?')})? → suggest a post
   Their current CTR: {perf.get('ctr', '?')} vs peer: {peer_stats.get('avg_ctr', '?')}
4. Loss framing: frame inaction as losing ground, not "you might want to..."
5. {lang_note}
6. Single CTA: "Want me to pull their profile + suggest what to do?" or similar

Output format: {{
  "body": "<150-320 chars: competitor hook → defensive action → single CTA>",
  "cta": "open_ended",
  "send_as": "vera",
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<voyeur frame + specific defensive action recommended>"
}}\
"""


def renewal_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    sub = merchant.get("subscription", {})

    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: renewal_due (internal, merchant-facing)
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ INSTRUCTIONS ═══
The merchant's subscription is expiring soon ({sub.get('days_remaining', '?')} days left).
• Lead with VALUE they've received, not with "your sub is expiring".
• Cite their actual performance numbers as evidence of value.
• Frame what they'd lose (profile maintenance pauses, visibility drops).
• send_as = "vera"
• CTA: binary_yes_stop"""


def curious_ask_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

═══ TREND SIGNALS ═══
{json.dumps(category.get('trend_signals', []), ensure_ascii=False)[:400]}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: curious_ask_due (internal, merchant-facing)
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ INSTRUCTIONS ═══
This is a curiosity-driven "ask the merchant" message. The goal is to learn \
something from them AND offer to turn their answer into content.
• Ask ONE specific question about their business this week.
• Offer to convert the answer into a Google post / WhatsApp template / social content.
• Keep it short — 2-3 sentences.
• send_as = "vera"
• CTA: open_ended"""


def generic_merchant_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    """Fallback for any trigger kind not explicitly handled."""
    perf = merchant.get("performance", {})
    delta = perf.get("delta_7d", {})
    
    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

{_conversation_history(merchant)}

═══ TRIGGER ═══
Kind: {trigger.get('kind', 'unknown')}
Source: {trigger.get('source', 'unknown')}
Scope: {trigger.get('scope', 'merchant')}
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Urgency: {trigger.get('urgency', '?')}
Suppression key: {trigger.get('suppression_key', '')}

═══ CRITICAL RULES ═══
1. **First sentence MUST explain "why am I messaging you RIGHT NOW?"**
   • Cite the trigger reason explicitly
   • Example: "Your calls dropped 22% this week — here's what I see"
   • NOT: "Hi, I noticed something..."

2. **Anchor on THEIR specific numbers from performance**
   • Their views: {perf.get('views', '?')}, calls: {perf.get('calls', '?')}, CTR: {perf.get('ctr', '?')}
   • 7d deltas: views {delta.get('views_pct', '?')}%, calls {delta.get('calls_pct', '?')}%
   • Compare to peer stats if relevant

3. **Every number must come from context**
   • Never fabricate percentages or counts
   • Extract directly from above merchant performance

4. **Final sentence: ONE concrete offer**
   • "Want me to draft X?" or "Reply YES"
   • NOT multiple CTAs

Output format: {{
  "body": "<150-350 chars>",
  "cta": "open_ended",
  "send_as": "vera",
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<why this trigger + their context = timely>"
}}"""


def perf_change_prompt(
    category: dict, merchant: dict, trigger: dict
) -> str:
    """Handle performance dips and spikes."""
    payload = trigger.get("payload", {})
    kind = trigger.get("kind", "perf_dip")
    perf = merchant.get("performance", {})
    delta = perf.get("delta_7d", {})
    
    # Determine if spike or dip
    is_spike = "spike" in kind
    direction = "📈 UP" if is_spike else "📉 DOWN"
    
    # Extract the specific metric that changed
    changed_metric = payload.get("metric", "performance")
    change_pct = payload.get("change_pct", 0)
    
    return f"""\
{_merchant_header(merchant)}

{_category_stats(category)}

═══ PERFORMANCE CHANGE ALERT ═══
Direction: {direction}
Metric: {changed_metric}
Change: {change_pct}%
Current: views={perf.get('views', '?')}, calls={perf.get('calls', '?')}, CTR={perf.get('ctr', '?')}
Peer median (30d): {category.get('peer_stats', {}).get('avg_ctr', '?')}

{_conversation_history(merchant)}

═══ TRIGGER: {kind} ═══

CRITICAL RULES:
1. **First sentence MUST name the change and metric**
   • If SPIKE: "Great news — your calls jumped 28% this week"
   • If DIP: "Your CTR dropped 15% this week — want to know why?"

2. **Explain the WHY**
   • Spike → celebrate, suggest replicating (more posts? new offer?)
   • Dip → diagnose (profile stale? offer expired? search volume down?)

3. **Compare to peer stats**
   • Your CTR: {perf.get('ctr', '?')} vs peer median: {category.get('peer_stats', {}).get('avg_ctr', '?')}
   • Position: "You're {'above' if perf.get('ctr', 0) > category.get('peer_stats', {}).get('avg_ctr', 0) else 'below'} peer median"

4. **Offer action**
   • Spike: "Want me to draft a follow-up offer to keep this momentum?"
   • Dip: "Want me to audit your profile against competitors?"

Output format: {{
  "body": "<150-350 chars>",
  "cta": "open_ended",
  "send_as": "vera",
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<performance trend + merchant fit>"
}}"""

"""
User-prompt builders for customer-facing compositions.

Messages sent on behalf of the merchant to their customer.
"""

from __future__ import annotations
import json


def _customer_header(customer: dict, merchant: dict) -> str:
    c_id = customer.get("identity", {})
    m_id = merchant.get("identity", {})
    rel = customer.get("relationship", {})
    offers = [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]

    return f"""\
═══ CUSTOMER ═══
Name: {c_id.get('name', 'Customer')}
Language: {c_id.get('language_pref', 'en')}
State: {customer.get('state', 'unknown')}
Last visit: {rel.get('last_visit', '?')} | Total visits: {rel.get('visits_total', '?')}
Preferred slots: {customer.get('preferences', {}).get('preferred_slots', 'any')}
Consent: {', '.join(customer.get('consent', {}).get('scope', []))}

═══ MERCHANT ═══
Name: {m_id.get('name', '')} | Owner: {m_id.get('owner_first_name', '')}
Locality: {m_id.get('locality', '')}, {m_id.get('city', '')}
Active offers: {', '.join(offers) if offers else 'None'}"""


def recall_due_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: recall_due ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Recall reminder from merchant to customer.
• send_as = "merchant_on_behalf". Address by name. Reference time since last visit.
• Include active offer price. Honor language pref. Offer 2 time slots.
• CTA: Reply 1/2 or YES. No medical claims. Keep short."""


def customer_lapsed_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: {trigger.get('kind', 'customer_lapsed')} ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Re-engage lapsed customer on behalf of merchant.
• send_as = "merchant_on_behalf". No guilt-tripping. Lead with something new.
• Reference past services. Include relevant offer. Honor language pref.
• CTA: low-friction. Keep warm and brief."""


def appointment_tomorrow_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: appointment_tomorrow ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Appointment reminder.
• send_as = "merchant_on_behalf". State date, time, service, merchant name + locality.
• CTA: "Reply CONFIRM or reschedule." Very short."""


def chronic_refill_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: chronic_refill_due ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Medication refill reminder.
• send_as = "merchant_on_behalf". Name medications if in payload.
• Include delivery option and discounts if applicable. Respectful tone.
• CTA: "Reply CONFIRM to dispatch." Clear and concise."""


def trial_followup_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: trial_followup ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Follow up after trial/first visit.
• send_as = "merchant_on_behalf". Ask about experience. Mention conversion offer.
• No pressure. CTA: open-ended. Keep warm and brief."""


def generic_customer_prompt(category: dict, merchant: dict, trigger: dict, customer: dict) -> str:
    return f"""{_customer_header(customer, merchant)}

═══ TRIGGER: {trigger.get('kind', 'unknown')} ═══
Payload: {json.dumps(trigger.get('payload', {}), ensure_ascii=False)}
Suppression key: {trigger.get('suppression_key', '')}

INSTRUCTIONS: Customer-facing message on behalf of merchant.
• send_as = "merchant_on_behalf". Address by name. Honor language pref.
• Include relevant offer. CTA: simple reply. Keep brief and warm."""

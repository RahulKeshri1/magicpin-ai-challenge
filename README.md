# Vera Bot — magicpin AI Challenge

**Team:** Solo Rahul (RahulKeshri1)
**Model:** Gemini 2.5 Flash (composition) + Llama 3.3 70B via Groq (classification)
**Contact:** rahulkumarkeshri475@gmail.com

---

## Approach

The bot uses a **dual-LLM architecture** built on the 4-context framework:

```
compose(category, merchant, trigger, customer?) → message
```

**Gemini 2.5 Flash** handles message composition. Its thinking capability is used deliberately — the 2048-token output budget lets it reason about which compulsion levers fit before writing. Each trigger kind routes to a dedicated prompt template (15+ variants) so `research_digest`, `perf_dip`, `competitor_opened`, and `recall_due` each get context-aware instructions rather than a single generic prompt.

**Groq Llama 3.3 70B** handles lightweight intent classification in reply flows. It's fast (< 2s) and cheap, which keeps the `/v1/reply` call well within the 30s judge timeout after Gemini already ran for the proactive message.

**Prompt engineering principles:**
- First sentence rule: must answer "why am I messaging you RIGHT NOW?" — trigger payload embedded in the opening
- Fabrication detection: all numbers in the composed body are checked against context; fabricated values trigger a retry with repair instructions
- Category voice injection: `vocab_allowed`, `vocab_taboo`, tone, and salutation patterns from `CategoryContext` are injected into the system prompt, not post-filtered
- Social proof + asking the merchant: both underused in production Vera — explicitly instructed in every prompt template as priority levers
- CTA enforcement: validator checks CTA is the last sentence; generic "X% off" patterns flagged and repaired

---

## Key Tradeoffs

| Decision | Why |
|---|---|
| Token-bucket rate limiter (proactive) | Free-tier APIs (15 RPM Gemini, 30 RPM Groq) fail in bursts; sliding-window deque avoids cascading 429s |
| Intent heuristics before LLM classifier | "Yes"/"haan kar do" patterns checked first — prevents the intent-handoff failure that production Vera has (see challenge brief §3) |
| Safety-net override on intent_action | LLM occasionally returns "end" when merchant says yes; overridden with action-mode response |
| Smart-quote normalisation + regex body extraction | Gemini sometimes emits curly quotes or wraps JSON in thinking blocks — brace-matching + regex fallback recovers the body without losing the message |
| Per-turn language detection | Devanagari character ratio detection — if merchant switches to Hindi mid-conversation, reply prompt switches to Hinglish automatically |
| Conditional retry (critical issues only) | Only re-prompts for `body_too_short` or `fabricated_numbers` — non-critical issues don't retry, keeping latency inside the 30s budget |

---

## Multi-turn Handling

- **Auto-reply detection:** 13 known canned-reply patterns + verbatim repeat detection; 2 consecutive auto-replies → `end`
- **Intent transitions:** heuristic + LLM; merchant saying "let's do it" routes directly to action mode, never back to qualification
- **Verbatim dedup:** `sent_bodies` set per conversation; returning the same message twice returns `end` instead of triggering the -2 judge penalty
- **Graceful exit:** 3 unanswered turns (basic plan) / 5 (Pro plan) → polite `end`

---

## What Additional Context Would Help Most

1. **Per-category compulsion lever rankings** — A/B test data on which of curiosity, social proof, and loss-aversion converts best for dentists vs restaurants vs gyms
2. **Merchant WhatsApp activity window** — knowing if a merchant typically reads messages at 9am vs 7pm would let the bot pick the right tick to send
3. **Historical auto-reply fingerprints** — merchant-level cache of known auto-reply strings to skip the LLM classifier entirely for repeat offenders

---

## Running Locally

```bash
cp .env.example .env          # add GEMINI_API_KEY and GROQ_API_KEY
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/v1/healthz
```

To regenerate `submission.jsonl` after the Gemini free-tier resets:
```bash
python generate_submission.py   # skips already-good entries, fills gaps
```

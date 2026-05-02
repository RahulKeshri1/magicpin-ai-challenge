# Vera — magicpin AI Challenge Submission

## Approach

**Dual-LLM architecture** with a 4-context composition framework:

1. **Gemini 2.5 Flash** (Google AI Studio) — message composition via structured prompts
2. **Groq Llama 3.3 70B** — lightweight intent classification (auto-reply, action, question, hostile)

### Architecture

```
Judge Harness → FastAPI Bot (5 endpoints)
                ├── Context Store (versioned, in-memory)
                ├── Conversation Registry (per-conv state tracking)
                └── Composer Engine
                    ├── Router (trigger.kind → prompt variant)
                    ├── Prompt Templates (merchant-facing / customer-facing)
                    ├── LLM Client (rate-limited, retries, timeout)
                    └── JSON Parser (brace-matching, fence-stripping)
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Token-bucket rate limiter** | Proactively avoids 429s instead of reacting — deque-based sliding window |
| **Intent detection before auto-reply** | Prevents false positives when merchant repeats a positive-intent message |
| **Safety-net on intent_action** | If LLM returns "end" on a confirmed action intent, overrides with an action-mode response |
| **Brace-matching JSON parser** | Gemini 2.5 Flash wraps output in markdown fences and thinking blocks; regex fails on nested JSON |
| **2048 output tokens** | Gemini's thinking model uses output budget for reasoning; 500 tokens truncates responses |
| **Category voice injection** | System prompt dynamically includes vocab_allowed, vocab_taboo, and salutation patterns from CategoryContext |

### What Additional Context Would Help

1. **More customer relationship data** — knowing purchase history, NPS score, and referral activity would enable more personalized customer-facing messages
2. **Merchant response patterns** — historical response rates by time-of-day and day-of-week for optimal send timing
3. **A/B test results** — which compulsion levers (curiosity vs loss-aversion vs social proof) work best per category
4. **WhatsApp template approval status** — knowing which templates are pre-approved would help craft compliant messages

## Quick Start

```bash
# 1. Create .env
cp .env.example .env
# Edit .env with your GEMINI_API_KEY and GROQ_API_KEY

# 2. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Generate dataset
cd dataset && python generate_dataset.py --seed-dir . --out ./expanded && cd ..

# 4. Run
uvicorn bot:app --host 0.0.0.0 --port 8080

# 5. Test
python judge_simulator.py
```

## Project Structure

```
├── bot.py                    # FastAPI — 5 endpoints
├── config.py                 # API keys, model config, rate limits
├── models.py                 # Pydantic request/response schemas
├── llm/
│   └── client.py             # Dual-LLM client + rate limiter
├── context/
│   └── store.py              # Versioned in-memory context store
├── conversation/
│   └── state.py              # Multi-turn state + intent detection
├── composer/
│   ├── engine.py             # Main composition orchestrator
│   ├── router.py             # Trigger kind → prompt mapping
│   └── prompts/
│       ├── base_system.py    # Shared system prompt
│       ├── merchant_facing.py # Per-kind merchant prompts
│       ├── customer_facing.py # Per-kind customer prompts
│       └── reply_handler.py  # Multi-turn reply prompt
├── dataset/                  # Provided dataset
├── requirements.txt
└── .env.example
```

## Team

- **Team**: Solo Rahul
- **Model**: Gemini 2.5 Flash + Llama 3.3 70B (Groq)

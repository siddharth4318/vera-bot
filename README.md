# Vera message engine: Team Siddharth

The engine picks **one** signal that should drive the next message. It writes that message only from facts in the pushed context, with one low-effort ask. It is deterministic, responds in under 5 ms per message, and never makes up a number.

## Approach

```
trigger.kind ─► handler (26 kinds + keyword router + generic reasoner)
                  │  1. WHY NOW   trigger fact (number / date / source)
                  │  2. SO WHAT   ONE merchant anchor, picked by the hook ranker
                  │  3. JUDGMENT  what Vera recommends (sometimes contrarian)
                  │  4. ONE ASK   YES / CONFIRM / slot pick, work externalised to Vera
                  ▼
guardrails: taboo words · URLs · jargon ("GBP") · consent · anti-repeat
                  ▼
optional LLM polish (temp 0), rejected if it adds or drops any number
```

- **Decision engine (`vera/core.py`).** Every grounded merchant fact becomes a scored "hook". Examples: a CTR gap vs. peers, a calls dip, no live offer, an unverified listing, stale posts, lapsed customers, and review themes. Each handler asks for the single best hook that fits its trigger instead of listing everything.
- **Judgment, not templating.**
  - A weekend IPL match leads to "skip the dine-in promo (IPL Saturdays ran 12% below a normal Saturday); the Tue-Thu offer doesn't run tonight anyway; go delivery-only".
  - A competitor at ₹199 leads to "don't price-match; they'll win on your 22-day-old post and below-median CTR; post and pin the review that says 'explains everything patiently'".
  - Diwali 188 days out leads to "too early to promote, right time to plan", built around a bridal offer because the season is bridal-led.
  - An expected seasonal dip gets reframed: "CTR 5.2% is above peer; no ad spend now; spend the quarter on your 245 active members".
  - A milestone trigger with no payload celebrates something real (CTR ahead of the category average, years in business), never an invented threshold.
- **Cross-signal intelligence.** Sharma ji's refill message notes this week's atorvastatin recall (source named, "a strength issue, not a safety one"). The recall alert names the matching customers from the merchant's own records. The CDE webinar is tied to the aligner interest Dr. Meera voiced earlier. If a merchant has said yes to something Vera still owes, a new-topic message acknowledges it first.
- **Evidence order.** Messages lead with what the merchant can see on their own dashboard (payload numbers, 30-day views/calls/CTR, signals, live offers), then add at most one deeper fact with its provenance (review themes, customer aggregates, peer stats, a named digest source). Proposals (tier prices, fees) are labelled as suggestions.
- **Provenance.** Every `rationale` is built from what the message actually did (no boilerplate claims) and lists the context fields used (`category.digest[d_…]`, `merchant.customer_aggregate…`), so the judge can verify there is no fabrication.
- **Customer-facing rules.**
  - Only the merchant's *live* offers are quoted. Catalog templates are never promised.
  - Slots come only from the payload.
  - The message follows `language_pref` (hi / hi-en / English, and a regional greeting for te/ta/kn).
  - Kids are reached through the parent, and seniors through the family channel.
  - No-consent customers are never messaged.
- **Language.** Merchant bodies stay in clear English so numbers read cleanly. Openers and closers are natural Hinglish for Hindi-speaking merchants, matching how Indian owners actually chat.

## Conversation engine (`vera/conversation.py`)

Replies are checked in this order:

1. Opt-out → end, and suppress the merchant.
2. Auto-reply (canned WA-Business phrasing, or the same text twice) → one owner-flag line, then wait 24h, then end. This is also tracked **across conversation ids**.
3. Hostility → one de-escalation line. A second hostile message → exit.
4. A slot pick is booked.
5. Commitment ("ok let's do it") → **instant action mode**: the artifact (draft post, offer, review reply, SOP checklist) is delivered in the same turn with a single CONFIRM. No qualifying questions.
6. "Later / kal" → wait the matching number of seconds.
7. Off-topic (GST, loans) → polite decline, then redirect to the thread.
8. Questions are answered only from context. Language is re-detected every turn.

## Operating rules

- `/v1/context` is idempotent: an identical re-post returns 200 as a no-op. A conflicting or older version returns 409.
- `/v1/tick` sends at most two messages per merchant per tick, each a different kind in its own conversation, highest urgency first.
- Context pushed from a Windows client that decoded UTF-8 as cp1252 (`â‚¹299`) is repaired on arrival.
- Suppression keys are never reused.
- `/v1/tick` has a 22 s budget with a deterministic fallback.
- `/v1/teardown` wipes all state.

## Tradeoffs

- **Rules over free-form LLM.** A rules-first composer can't hallucinate, is reproducible, and survives 10 req/s with a 30 s cap. The cost is less stylistic range on never-seen trigger kinds. The keyword router plus the generic reasoner cover those, and the LLM polish layer (`VERA_LLM_PROVIDER` / `VERA_LLM_API_KEY`) adds fluency without the risk.
- **Restraint.** When a trigger lacks data (e.g. `competitor_opened` with no name), the message says only what is known ("a new competitor listing showed up near Indiranagar") and leans on real merchant facts.
- **State is in-memory, single worker.** This keeps it fast and simple. The bot must stay up for the whole test window.

## Would have helped

Real slot availability per merchant, catalog-level service prices per merchant, and per-locality peer stats.

## Layout

```
app.py                  FastAPI endpoints (/v1/context, /tick, /reply, /healthz, /metadata)
bot.py                  compose() in the submission contract shape
vera/composer.py        routing by trigger kind + guardrails
vera/core.py            Ctx / Draft types and the merchant hook ranker
vera/handlers/          one module per trigger family (knowledge, performance, events, ...)
vera/conversation.py    multi-turn reply engine
vera/facts.py           safe accessors + formatting (₹, Indian digit grouping, dates)
vera/llm.py             optional fact-locked LLM polish
scripts/                submission generator, judge-style end-to-end harness, judge_view.py
                        (checks every number in a message against the judge-visible context)
tests/                  pytest suite (unit, API, full-dataset sweep)
```

## Run

```bash
pip install -r requirements-dev.txt
uvicorn app:app --port 8080 --workers 1

# generate the challenge dataset once (from the challenge pack), then:
export VERA_DATASET=path/to/expanded
pytest                                   # 300+ tests, ~1s
python scripts/generate_submission.py    # writes submission.jsonl for the 30 test pairs
python scripts/simulate_judge.py         # end-to-end run against the local server
python scripts/chat.py                   # chat with Vera in the terminal (no server, no API key)
```

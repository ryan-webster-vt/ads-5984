# `travel_desk.py` — Block-by-Block Explanation

This file builds a **multi-agent travel booking desk** as a LangGraph state machine.
Four LLM agents each make exactly one structured decision; a deterministic
`coordinator` function is the only thing that writes to shared state and the only
thing that ever touches a price.

The file is organized into nine numbered comment banners. This document walks
through them in order, and also covers the blocks inside each section.

---

## Block 0 — Module docstring (lines 1–17)

*(Not a numbered section in the source; it comes before banner 1.)*

Describes the design contract up front:

- The traveler fills in a **booking form** (origin, destination, dates, budget) plus
  free-text notes.
- Four agents:
  - **router** — reads the notes and decides which desks the trip needs.
  - **flight desk** — picks a flight from its own inventory.
  - **hotel desk** — picks a hotel from its own inventory.
  - **resolver** — decides what to trade down when the trip is over budget.
- A deterministic **coordinator** is the single writer of the itinerary. It looks up
  prices, keeps hotel dates aligned with the flight, checks the budget, books or
  escalates, and decides which node runs next.
- Key architectural rule: **LangGraph owns control flow; each LLM call returns one
  structured decision and nothing else.**

---

## Block 1 — Imports (lines 19–32)

```python
import os
import time
from datetime import date
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ValidationError
from langgraph.graph import StateGraph, START, END
```

- `os` — read the three model-related environment variables (`OPEN_AI_ENDPOINT`,
  `OPEN_AI_API_KEY`, `OPEN_AI_MODEL`).
- `time` — used only for the rate-limit backoff (`time.sleep`).
- `date` — date arithmetic for hotel nights.
- `Literal`, `Optional`, `TypedDict` — type hints: constrained string choices,
  nullable fields, and the graph's state shape.
- `BaseModel`, `ValidationError` — Pydantic schemas for agent output and validation.
- `StateGraph`, `START`, `END` — LangGraph primitives.

### The guarded `openai` import

```python
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
```

The OpenAI client is imported **defensively** so the module can be imported and the
graph can be tested **without credentials installed**. The real network call lives in
`_call_once`; tests inject a fake `call_fn` instead.

---

## Block 2 — The inventories (lines 35–82, source section 1)

### `FLIGHTS` (lines 39–65)

A dict keyed by destination airport code (`"ORD"`, `"DEN"`), each holding a list of
flight option dicts:

| field | meaning |
|---|---|
| `id` | inventory key the agent must return (e.g. `"UA412"`) |
| `price` | **trip total** in dollars |
| `depart` / `arrive` | ISO-ish `"YYYY-MM-DD HH:MM"` strings |
| `back` | return flight datetime |
| `note` | human-readable description shown to the model |

The comment above it states the central rule: **prices live here and only here. No
agent ever supplies or computes a price.**

### `HOTELS` (lines 67–82)

Same shape, keyed by city, with:

- `id`, `name`, `note`
- `nightly` — a **per-night** rate (contrast with flights' trip-total `price`).

That `price` vs `nightly` difference is the reason the coordinator and the desk code
handle arithmetic themselves instead of trusting the model.

### `find()` (lines 85–86)

```python
def find(inventory: dict, city: str, option_id: str) -> Optional[dict]:
    return next((o for o in inventory[city] if o["id"] == option_id), None)
```

Looks up an option by ID within a city's list, returning `None` if absent. This is how
the coordinator converts an agent's **ID-only** answer into a real record (and its
price) — an agent can never smuggle in a made-up price.

### `stay_dates()` (lines 89–93)

```python
def stay_dates(request, flight):
    if flight is None:
        return request["depart_date"], request["return_date"]
    return flight["arrive"][:10], flight["back"][:10]
```

Determines hotel check-in / check-out dates. If a flight is held, check in on the day
the flight **lands** and check out on the day it **flies home**. Otherwise fall back to
the form's dates. Slicing `[:10]` keeps just the `YYYY-MM-DD` part.

This is the coupling that prevents the classic bug of a hotel whose nights don't match
the flight (e.g. an overnight flight landing the next morning).

### `quote_hotel()` (lines 96–100)

```python
nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
return {**hotel, "check_in": ..., "check_out": ..., "nights": nights,
        "total": hotel["nightly"] * nights}
```

Turns a raw hotel record into a **quote**: adds `check_in`, `check_out`, `nights`, and
`total`. All money math is done by code, not by the model.

---

## Block 3 — Structured outputs (lines 103–125, source section 2)

These Pydantic models are the **only** shapes an agent is allowed to emit. They are
passed as the `response_format` schema, so the model is forced into a decision object
rather than prose.

### `RoutePlan` (lines 106–112)

The router's output:

- `services: list[Literal["flight", "hotel"]]` — what the trip needs.
- `flight_notes` / `hotel_notes` — the subset of traveler preferences each desk
  should see (each agent sees only what its job needs).
- `unsupported: list[str]` — things asked for that the desk can't book (cars, tours…).
- `message` — one sentence for the itinerary log.

### `Pick` (lines 115–118)

A desk's answer: `option_id` (an inventory ID) plus a short `message`. **There is no
price field** — deliberately, so a desk cannot invent one.

### `Resolution` (lines 121–124)

The resolver's answer: `action` is one of `trade_down_flight`,
`trade_down_hotel`, or `escalate`, plus a `message`.

---

## Block 4 — Shared state (lines 128–143, source section 3)

`TripState` is a `TypedDict` — the "itinerary sheet" every node reads and (only the
coordinator) writes:

| field | role |
|---|---|
| `request` | the booking form, read-only |
| `plan` | router's plan, recorded by the coordinator |
| `log` | append-only itinerary log (`{"agent", "message"}` entries) |
| `pending` | the latest agent output, awaiting coordinator review |
| `flight` | the held flight (coordinator writes it) |
| `hotel` | the held hotel quote (coordinator writes it) |
| `trade_down` | `"flight"` / `"hotel"` while resolving |
| `next` | which node should run next |
| `round` | resolution rounds used so far |
| `max_rounds` | the budget on retries — the guarantee the loop stops |
| `status` | `"in_progress"` / `"booked"` / `"escalated"` |
| `total` | current trip total |
| `reason` | why the trip was escalated |

The `pending` field is the core pattern: agents never mutate `flight`/`hotel`; they
leave a proposal in `pending`, and the coordinator validates and commits it.

---

## Block 5 — The model call (lines 145–208, source section 4)

### `llm_call()` (lines 149–169)

A retry wrapper around one structured model call.

- Loops up to 20 times (~10 minutes worst case).
- If the exception text contains `"Rate limit"`, sleep 30 s and retry — because VT ARC
  caps the model at roughly 30 requests/minute and every turn costs a call.
- If it contains `"unavailable"` or (case-insensitively) `"disconnected"`, sleep 10 s
  and retry — riding out transient backend blips.
- Any other exception is re-raised immediately.
- If all 20 attempts fail, raise `RuntimeError("rate-limited for too long; giving up")`.

`agent` is accepted as a label (useful for logging/testing) but the actual call only
needs `system`, `user`, and `schema`.

### `_call_once()` (lines 172–208)

Does one real attempt:

1. Builds an `OpenAI` client from `OPEN_AI_ENDPOINT` / `OPEN_AI_API_KEY` and reads
   `OPEN_AI_MODEL`. Creating the client per call keeps it credential-driven and
   test-friendly.
2. Sends `system` + `user` messages and calls
   `client.chat.completions.parse(..., response_format=schema, temperature=0.4)`.
3. Returns `completion.choices[0].message.parsed` if present.

**The endpoint-quirk fallback (lines 189–208):** VT ARC's server sometimes mangles
strict-mode `.parse()` output (a stray leading `{`). If validation fails, the code:

1. Retries with a plain, non-strict `json_schema` response format built from
   `schema.model_json_schema()`.
2. Validates the returned text with `schema.model_validate_json`.
3. On failure, finds the **second** `{` and re-parses from there — repairing the known
   stray-brace corruption. If there is no second `{`, it re-raises.

So even a flaky backend still yields a validated Pydantic object.

---

## Block 6 — The agents (lines 211–324, source section 5)

Each `make_*` factory takes `call_fn` and returns a LangGraph node function. This
**dependency injection** is what lets the whole graph run offline with a fake model.

### `make_router()` (lines 214–234)

- `system` prompt: the desk can book **only** flights and hotels. Always include a
  flight for a city-to-city trip, and a hotel unless the notes say the traveler has
  somewhere to stay. Anything else (cars, tours, restaurants) goes in `unsupported`.
  Keep `message` to one sentence.
- `user` prompt: origin → destination, dates, and the traveler's notes.
- Calls the model for a `RoutePlan`.
- `services = sorted(set(plan.services))` — dedupes and puts them in a fixed order
  (`flight` before `hotel`), which makes routing deterministic.
- Returns `{"pending": {...}}` — it does **not** write `plan` itself; the coordinator
  records it.

### `make_desk(kind, call_fn)` (lines 237–288)

One factory builds both the flight and hotel desks from `kind`.

**Setup (lines 239–240):** picks the right inventory (`FLIGHTS`/`HOTELS`) and the
right price key (`price`/`nightly`).

**Node body:**

1. **Choose the option list (lines 243–248).** Normally all options for the
   destination. If we're trading down *this* kind, keep only options **strictly
   cheaper** than the one held.
2. **No options → no model call (lines 249–252).** Returns a `pending` with
   `option_id: None` and "no cheaper X available". Saves a call and avoids asking the
   model an impossible question.
3. **Format the option lines (lines 254–256).** `- ID: $price[/night] — note`.
4. **Add savings arithmetic while trading down (lines 257–263).** When resolving, each
   line is annotated with how much that option **saves on this trip**:
   - flight: `(held_price - option_price) * 1`
   - hotel: `(held_nightly - option_nightly) * held["nights"]`

   This math is done in **code** because models confuse a per-night rate with a trip
   total.
5. **Build the prompt (lines 265–274).** `system` says "you are the {kind} desk,
   choose the one that best fits, reply with `option_id` exactly as listed plus one
   sentence." `user` gives only that desk's notes and the option lines. When trading
   down, it appends the exact overage (`total - budget`) and asks for an option that
   saves at least that much while respecting preferences.
6. **Call the model** for a `Pick`.
7. **Output guardrail (lines 277–287).** Build the set of offered IDs. If the returned
   `option_id` isn't in it (hallucinated, or too expensive while trading down), replace
   it with the **cheapest listed option** and prefix the message with
   `[guard: '<bad id>' not offered]`. The model chooses; it can never invent an option
   or a price.
8. Return the result as `pending`.

### `make_resolver()` (lines 291–324)

- Gathers what's held: flight line (`id`, `price`, note) and hotel line (`name`,
  `nightly × nights = total`, note).
- Builds a history string from the itinerary log (`agent: message` per line).
- `system` prompt: the trip is over budget; choose which booking to trade down,
  giving up what matters least. **Preferences are wishes; the budget is a hard limit.**
  Escalate to a human **only** if the log shows neither booking can get cheaper.
- `user` prompt: notes, budget, held total, held items, log so far.
- Calls the model for a `Resolution`.
- **Consistency guard (lines 317–321):** if the action is a trade-down of a service
  the trip doesn't include (e.g. trade down a hotel on a flight-only trip), redirect it
  to `trade_down_<first included service>`.
- Returns `pending` with the action and message.

---

## Block 7 — The coordinator (lines 326–396, source section 6)

`coordinator(state) -> dict` is the hub of the graph and the **single writer** of the
itinerary. It runs after the router and after every agent. It has three phases:

### (a) Commit the pending agent output (lines 339–363)

First, copy state into locals (`log`, `plan`, `flight`, `hotel`, `trade_down`, `rnd`,
`status`, `reason`, `total`, `nxt`, `pm`).

If `pending` is not `None`:

- Append `{agent, message}` to the log.
- **router** → store the plan. If `services` is empty, mark `escalated` with reason
  `"nothing here we can book"`.
- **flight** → if an ID was picked, look it up with `find(FLIGHTS, ...)` and hold the
  **inventory record** (so the price comes from the table, not the model).
  - **(b) Re-price the held hotel** with `quote_hotel(hotel, *stay_dates(r, flight))`
    so its check-in/out and night count match the newly held flight.
  - Clear `trade_down`.
- **hotel** → quote the picked hotel against the current flight's dates, hold it, clear
  `trade_down`.
- **resolver** → `escalate` sets status + reason; otherwise set
  `trade_down` to `"flight"` or `"hotel"`.

### (c) Decide what happens next (lines 365–387)

Only if still `in_progress`:

1. If `trade_down` is set → go back to that desk.
2. Else if a flight is required but not held → `next = "flight"`.
3. Else if a hotel is required but not held → `next = "hotel"`.
4. Else both needs are satisfied → compute
   `total = flight.price + hotel.total` (0 for anything absent).
   - `total <= budget` → `status = "booked"`, log `booked for $<total>`.
   - Otherwise → increment `round`, `next = "resolver"`. If `round > max_rounds`,
     set `status = "escalated"` and `reason` = "still $X over budget after N rounds".
     **This is the loop guard.**

### Escalation logging (lines 389–391)

If status *became* escalated this turn (`state["status"] != "escalated"`), append
`coordinator: escalated to a human` exactly once.

### Return (lines 393–396)

Returns the updated fields, clears `pending` back to `None`, and leaves `request` and
`max_rounds` untouched. Because it returns the whole set each time, state transitions
are explicit and easy to audit.

---

## Block 8 — The route function (lines 399–405, source section 7)

```python
def route(state):
    if state["status"] != "in_progress":
        return "end"
    return state["next"]
```

This is the **conditional edge** out of the coordinator. Once the trip is booked or
escalated it returns `"end"`; otherwise it forwards the coordinator's `next` choice.
It has no side effects — pure routing.

---

## Block 9 — Graph construction (lines 407–424, source section 8)

`build_travel_desk(call_fn=llm_call)`:

1. `g = StateGraph(TripState)` — state schema.
2. Adds five nodes: `router`, `coordinator`, `flight`, `hotel`, `resolver`
   (agents built by the factories with the injected `call_fn`).
3. **Edges:**
   - `START → router`
   - `router → coordinator`
   - `coordinator` → conditional edge via `route`, mapping
     `flight`/`hotel`/`resolver` to their nodes and `end` to `END`.
   - Each of `flight`, `hotel`, `resolver` → back to `coordinator`.

Result: a **hub-and-spoke** topology. Control always returns to the coordinator, so
there is exactly one place that mutates the itinerary and one place that decides what
runs next.

```
START → router → coordinator ─┬→ flight ──┐
                              ├→ hotel ───┤
                              ├→ resolver ┤
                              └→ END      │
                                 ↑─────────┘
```

---

## Block 10 — `run()` (lines 427–436, source section 9, first half)

```python
def run(request, max_rounds=4, call_fn=llm_call):
    app = build_travel_desk(call_fn=call_fn)
    init = { ...all state fields initialized... }
    return app.invoke(init, config={"recursion_limit": 60})
```

- Builds a fresh compiled graph (so a custom `call_fn` can be injected per run).
- Initializes every `TripState` field: no plan, empty log, nothing held, `next`
  `"router"`, `round` 0, `status` `"in_progress"`.
- Invokes the graph with **`recursion_limit: 60`** — LangGraph's own hard stop, a second
  safety net in addition to `max_rounds`.

---

## Block 11 — Scenarios and the harness (lines 439–499, source section 9, second half)

### `SCENARIOS` (lines 442–466)

Three named test cases, each with a `request`, the expected `services`, and
`max_rounds`:

| name | route | budget | what it exercises |
|---|---|---|---|
| `conference` | ROA → ORD, Nov 9–12 | $1400 | comfortable budget, prefer nonstop + walkable hotel → should book a premium itinerary |
| `shoestring` | ROA → ORD, Nov 9–12 | $600 | tight budget, cheapest of everything → should trade down and still book |
| `family` | ROA → DEN, Dec 19–27 | $700 | staying with a sister, only a flight needed, **and** an unsupported rental-car request → flight-only booking, car noted as unsupported |

### `cheapest_trip()` (lines 469–481)

The **ground truth**: the minimum possible cost of any itinerary that includes the
requested services. It brute-forces every flight × hotel combination, using `0`/`None`
lists when a service isn't needed, and calls `quote_hotel` with `stay_dates` so hotel
night counts match each candidate flight. This is what decides whether a booking is
even *possible*.

### `check()` (lines 484–499)

The pass/fail oracle:

- `feasible = cheapest_trip(...) <= budget`.
- If feasible: **PASS** only when `status == "booked"`, `total <= budget`, and exactly
  the requested services were booked.
- If not feasible: **PASS** only when `status == "escalated"`.
- Prints a line with status, total, booked list, and the expectation, and returns the
  boolean.

This gives the file a checkable "known answer" independent of the LLM's choices —
the same idea as a unit-test harness.

---

## Block 12 — `__main__` (lines 502–514, source section 9, third part)

For each scenario:

1. Print a header with the budget.
2. `run(...)` the graph.
3. Print every log entry as `agent: message`.
4. If the plan listed `unsupported` requests, print them as `(not handled: ...)`.
5. Print the uppercase verdict (`BOOKED` / `ESCALATED`), appending the reason when
   escalated.
6. Call `check(...)` to print PASS/FAIL.

Running `python travel_desk.py` therefore executes three end-to-end bookings and
verifies each against the deterministic ground truth.

---

## Cross-cutting design takeaways

1. **Agents propose, coordinator commits.** Agents return `pending`; only the
   coordinator writes `flight`, `hotel`, `plan`, `log`, and `status`.
2. **Prices live in one place.** Agents emit IDs; `find()` resolves them to inventory
   records, so a hallucinated price is impossible.
3. **Code does the arithmetic.** Night counts, hotel totals, savings from a trade-down,
   and the overage are all computed in Python, never by the model.
4. **Every loop has a budget.** `max_rounds` in state plus `recursion_limit` in the
   invoke config guarantee termination.
5. **Guardrails are post-hoc and deterministic.** Bad option IDs get replaced by the
   cheapest valid option; impossible trade-down targets get redirected.
6. **Testability by injection.** `call_fn` flows through every factory and into `run`,
   so the graph can be exercised with a fake model and no credentials.

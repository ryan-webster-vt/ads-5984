"""
travel_desk.py — A multi-agent TRAVEL DESK built as a LangGraph state machine.

A traveler fills in a booking form (cities, dates, budget) and adds free-text
notes. Four agents work the request:

  - the ROUTER reads the notes and decides which desks the trip needs,
  - the FLIGHT desk picks a flight from the flight inventory it owns,
  - the HOTEL desk picks a hotel from the hotel inventory it owns,
  - the RESOLVER decides what to give up when the trip is over budget.

A deterministic COORDINATOR is the only writer of the shared itinerary. It
looks up every price itself, keeps the hotel dates matched to the flight,
checks the budget, books the trip or escalates it to a human, and routes the
next turn. LangGraph owns the control flow; each LLM call produces one
structured decision and nothing else.
"""

import os
import time
from datetime import date
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, ValidationError
from langgraph.graph import StateGraph, START, END

# The openai client is created lazily so the graph can be imported (and
# tested) without credentials. The live model call is `llm_call` below.
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# --------------------------------------------------------------------------- #
# 1. The inventories — each desk's private tool data                          #
#    Prices live HERE and only here. No agent ever supplies a price.          #
# --------------------------------------------------------------------------- #
FLIGHTS = {
    "ORD": [
        dict(id="UA412", price=612, depart="2026-11-09 07:10",
             arrive="2026-11-09 08:40", back="2026-11-12 18:05",
             note="United, nonstop, morning departure"),
        dict(id="DL2210", price=497, depart="2026-11-09 06:00",
             arrive="2026-11-09 10:20", back="2026-11-12 19:40",
             note="Delta, 1 stop via ATL"),
        dict(id="AA5520", price=438, depart="2026-11-09 11:30",
             arrive="2026-11-09 15:55", back="2026-11-12 17:15",
             note="American, 1 stop via CLT, afternoon arrival"),
        dict(id="AA5871", price=389, depart="2026-11-09 21:50",
             arrive="2026-11-10 06:05", back="2026-11-12 17:15",
             note="American, 1 stop via CLT, overnight, lands next morning"),
    ],
    "DEN": [
        dict(id="UA1530", price=544, depart="2026-12-19 08:00",
             arrive="2026-12-19 12:30", back="2026-12-27 14:00",
             note="United, 1 stop via IAD"),
        dict(id="DL880", price=521, depart="2026-12-19 06:45",
             arrive="2026-12-19 11:10", back="2026-12-27 16:20",
             note="Delta, 1 stop via ATL"),
        dict(id="AA3307", price=468, depart="2026-12-19 12:15",
             arrive="2026-12-19 17:50", back="2026-12-27 10:30",
             note="American, 1 stop via CLT"),
    ],
}

HOTELS = {
    "ORD": [
        dict(id="HMP", name="Hilton McCormick Place", nightly=289,
             note="connected to the McCormick Place convention center"),
        dict(id="HPS", name="Hyatt Place South Loop", nightly=199,
             note="0.8 mile walk to McCormick Place"),
        dict(id="MBI", name="Midway Budget Inn", nightly=109,
             note="9 miles from McCormick Place, near Midway airport"),
    ],
    "DEN": [
        dict(id="DUS", name="Hotel Union Station", nightly=319,
             note="downtown Denver"),
        dict(id="DAE", name="Airport Express Inn", nightly=129,
             note="next to Denver International"),
    ],
}


def find(inventory: dict, city: str, option_id: str) -> Optional[dict]:
    return next((o for o in inventory[city] if o["id"] == option_id), None)


def stay_dates(request: dict, flight: Optional[dict]) -> tuple[str, str]:
    """Check in when the flight LANDS, check out when it flies home."""
    if flight is None:
        return request["depart_date"], request["return_date"]
    return flight["arrive"][:10], flight["back"][:10]


def quote_hotel(hotel: dict, check_in: str, check_out: str) -> dict:
    nights = (date.fromisoformat(check_out)
              - date.fromisoformat(check_in)).days
    return {**hotel, "check_in": check_in, "check_out": check_out,
            "nights": nights, "total": hotel["nightly"] * nights}


# --------------------------------------------------------------------------- #
# 2. The structured outputs — the only things an agent may emit               #
# --------------------------------------------------------------------------- #
class RoutePlan(BaseModel):
    """The router's decision. Same structured-output idea as Lab 2."""
    services: list[Literal["flight", "hotel"]]
    flight_notes: str        # the preferences the flight desk should see
    hotel_notes: str         # the preferences the hotel desk should see
    unsupported: list[str]   # anything asked for that we cannot book
    message: str             # one sentence for the itinerary log


class Pick(BaseModel):
    """A desk's choice: an inventory ID, never a price."""
    option_id: str
    message: str


class Resolution(BaseModel):
    """The resolver's decision when the trip is over budget."""
    action: Literal["trade_down_flight", "trade_down_hotel", "escalate"]
    message: str


# --------------------------------------------------------------------------- #
# 3. The shared state — the itinerary sheet                                   #
# --------------------------------------------------------------------------- #
class TripState(TypedDict):
    request: dict                # the booking form (read-only)
    plan: Optional[dict]         # the router's plan, recorded by coordinator
    log: list[dict]              # append-only itinerary log
    pending: Optional[dict]      # an agent's latest output, awaiting review
    flight: Optional[dict]       # held flight (coordinator writes it)
    hotel: Optional[dict]        # held hotel quote (coordinator writes it)
    trade_down: Optional[str]    # "flight" | "hotel" while resolving
    next: str                    # which node runs next
    round: int                   # resolution rounds used so far
    max_rounds: int              # the budget — what guarantees we stop
    status: str                  # "in_progress" | "booked" | "escalated"
    total: Optional[float]
    reason: Optional[str]        # why the trip was escalated

# --------------------------------------------------------------------------- #
# 4. The model call — one structured decision per call                        #
#    `call_fn` is injectable everywhere so the graph can be tested offline.   #
# --------------------------------------------------------------------------- #
def llm_call(agent: str, system: str, user: str, schema):
    """Ask vt-arc-llm for one structured decision (Lab 2's technique).

    VT ARC caps vt-arc-llm at ~30 requests/minute, and every turn costs a
    call — so back-to-back runs (and especially Lab 8's harness) trip the
    cap. Wait out the window and retry rather than crashing mid-booking;
    also ride out transient backend blips.
    """
    for _wait in range(20):                    # generous: up to ~10 minutes
        try:
            return _call_once(system, user, schema)
        except Exception as exc:
            msg = str(exc)
            if "Rate limit" in msg:
                time.sleep(30)
                continue
            if "unavailable" in msg or "disconnected" in msg.lower():
                time.sleep(10)
                continue
            raise
    raise RuntimeError("rate-limited for too long; giving up")


def _call_once(system: str, user: str, schema):
    client = OpenAI(
        base_url=os.environ["OPEN_AI_ENDPOINT"],
        api_key=os.environ["OPEN_AI_API_KEY"],
    )
    model = os.environ["OPEN_AI_MODEL"]
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    try:
        completion = client.chat.completions.parse(
            model=model, messages=messages,
            response_format=schema, temperature=0.4,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is not None:
            return parsed
        raise ValueError("no parsed content")
    except (ValidationError, ValueError):
        # ENDPOINT QUIRK (same as Lab 2): strict-mode .parse() is often
        # mangled by the VT ARC server (a stray leading '{'). Fall back to a
        # plain (non-strict) json_schema request and validate by hand,
        # repairing the known stray-brace corruption if it appears.
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.4,
            response_format={"type": "json_schema",
                             "json_schema": {
                                 "name": schema.__name__,
                                 "schema": schema.model_json_schema()}},
        )
        text = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(text)
        except ValidationError:
            second = text.find("{", text.find("{") + 1)
            if second == -1:
                raise
            return schema.model_validate_json(text[second:])


# --------------------------------------------------------------------------- #
# 5. The agents — each sees only what its job needs                           #
# --------------------------------------------------------------------------- #
def make_router(call_fn):
    def router(state: TripState) -> dict:
        r = state["request"]
        system = (
            "You are the intake router for a travel desk that can book ONLY "
            "flights and hotels. List in `services` every one of those the "
            "traveler needs: always a flight for a trip between two cities, "
            "and a hotel unless the notes say they have somewhere to stay. "
            "List anything else they ask for (cars, tours, restaurants) in "
            "`unsupported`. Pass each desk only the preferences it needs. "
            "Keep `message` to one short sentence."
        )
        user = (f"Trip: {r['origin']} to {r['destination']}, "
                f"{r['depart_date']} to {r['return_date']}.\n"
                f"Traveler notes: {r['notes']}")
        plan = call_fn("router", system, user, RoutePlan)
        services = sorted(set(plan.services))       # dedupe, fixed order
        return {"pending": {"agent": "router", "message": plan.message,
                            "plan": {**plan.model_dump(),
                                     "services": services}}}
    return router


def make_desk(kind: str, call_fn):
    """Build the flight desk or the hotel desk. Each owns ONE inventory."""
    inventory = FLIGHTS if kind == "flight" else HOTELS
    price_key = "price" if kind == "flight" else "nightly"

    def desk(state: TripState) -> dict:
        r, held = state["request"], state[kind]
        options = inventory[r["destination"]]
        if state["trade_down"] == kind:
            # Resolving: only options strictly cheaper than what we hold.
            options = [o for o in options
                       if o[price_key] < held[price_key]]
        if not options:
            # Nothing to choose from, so no model call is needed at all.
            return {"pending": {"agent": kind, "option_id": None,
                                "message": f"no cheaper {kind} available"}}

        lines = [f"- {o['id']}: ${o[price_key]}"
                 f"{'' if kind == 'flight' else '/night'} — {o['note']}"
                 for o in options]
        if state["trade_down"] == kind:
            # The CODE does the arithmetic: what each option saves on
            # this trip. (Models confuse per-night rates with trip totals.)
            per = 1 if kind == "flight" else held["nights"]
            lines = [f"{line} (saves "
                     f"${(held[price_key] - o[price_key]) * per})"
                     for line, o in zip(lines, options)]
        lines = "\n".join(lines)
        notes = state["plan"][f"{kind}_notes"]
        system = (f"You are the {kind} desk of a travel agency. Choose the "
                  f"one {kind} that best fits the traveler. Reply with its "
                  f"option_id exactly as listed and one short sentence.")
        user = f"Traveler preferences: {notes}\nOptions:\n{lines}"
        if state["trade_down"] == kind:
            over = state["total"] - r["budget"]
            user += (f"\nThe trip is ${over} over budget. Prefer an "
                     f"option that saves at least ${over} and still "
                     f"respects the preferences.")
        pick = call_fn(kind, system, user, Pick)

        # Output guardrail (Module 7): an ID that is not on the list we
        # offered (hallucinated, or too expensive while trading down) is
        # replaced with the cheapest listed option. The model chooses;
        # it can never invent an option or a price.
        ids = {o["id"] for o in options}
        option_id, message = pick.option_id, pick.message
        if option_id not in ids:
            option_id = min(options, key=lambda o: o[price_key])["id"]
            message = f"[guard: {pick.option_id!r} not offered] {message}"
        return {"pending": {"agent": kind, "option_id": option_id,
                            "message": message}}
    return desk


def make_resolver(call_fn):
    def resolver(state: TripState) -> dict:
        r, f, h = state["request"], state["flight"], state["hotel"]
        held = []
        if f:
            held.append(f"flight {f['id']} ${f['price']} ({f['note']})")
        if h:
            held.append(f"hotel {h['name']} ${h['nightly']}/night x "
                        f"{h['nights']} = ${h['total']} ({h['note']})")
        history = "\n".join(f"{m['agent']}: {m['message']}"
                            for m in state["log"])
        system = (
            "You resolve conflicts for a travel desk. The held trip is over "
            "budget. Choose which booking to trade down to a cheaper option, "
            "giving up whatever matters least to the traveler. Preferences "
            "are wishes; only the budget is a hard limit. Escalate to a "
            "human agent ONLY if the log shows neither booking can get "
            "cheaper. "
            "Keep `message` to one or two short sentences."
        )
        user = (f"Traveler notes: {r['notes']}\n"
                f"Budget: ${r['budget']}. Held total: ${state['total']}.\n"
                f"Held: {'; '.join(held)}\nLog so far:\n{history}")
        res = call_fn("resolver", system, user, Resolution)
        action = res.action
        # A trade-down of a service the trip does not include is
        # meaningless; point it at a service the trip does include.
        services = state["plan"]["services"]
        if (action != "escalate"
                and action.removeprefix("trade_down_") not in services):
            action = f"trade_down_{services[0]}"
        return {"pending": {"agent": "resolver", "action": action,
                            "message": res.message}}
    return resolver

# --------------------------------------------------------------------------- #
# 6. The coordinator — single writer, price checker, budget keeper          #
# --------------------------------------------------------------------------- #
def coordinator(state: TripState) -> dict:
    r = state["request"]
    city = r["destination"]
    log = list(state["log"])
    plan, flight, hotel = state["plan"], state["flight"], state["hotel"]
    trade_down, rnd = state["trade_down"], state["round"]
    status, reason, total = state["status"], state["reason"], state["total"]
    nxt = state["next"]
    pm = state["pending"]

    # (a) Record the output that just arrived, looking up every price.
    if pm is not None:
        log.append({"agent": pm["agent"], "message": pm["message"]})
        if pm["agent"] == "router":
            plan = pm["plan"]
            if not plan["services"]:
                status, reason = "escalated", "nothing here we can book"
        elif pm["agent"] == "flight":
            if pm["option_id"] is not None:
                flight = find(FLIGHTS, city, pm["option_id"])
                # (b) A new flight can move the dates. Re-price the hotel
                # we already hold so its nights match the new flight.
                if hotel is not None:
                    hotel = quote_hotel(hotel, *stay_dates(r, flight))
            trade_down = None
        elif pm["agent"] == "hotel":
            if pm["option_id"] is not None:
                hotel = quote_hotel(find(HOTELS, city, pm["option_id"]),
                                    *stay_dates(r, flight))
            trade_down = None
        elif pm["agent"] == "resolver":
            if pm["action"] == "escalate":
                status, reason = "escalated", pm["message"]
            else:
                trade_down = pm["action"].removeprefix("trade_down_")

    # (c) Decide what happens next.
    if status == "in_progress":
        services = plan["services"]
        if trade_down is not None:
            nxt = trade_down
        elif "flight" in services and flight is None:
            nxt = "flight"
        elif "hotel" in services and hotel is None:
            nxt = "hotel"
        else:
            total = ((flight["price"] if flight else 0)
                     + (hotel["total"] if hotel else 0))
            if total <= r["budget"]:
                status = "booked"
                log.append({"agent": "coordinator",
                            "message": f"booked for ${total}"})
            else:
                rnd += 1
                nxt = "resolver"
                if rnd > state["max_rounds"]:
                    status = "escalated"
                    reason = (f"still ${total - r['budget']} over budget "
                              f"after {state['max_rounds']} rounds")

    if status == "escalated" and state["status"] != "escalated":
        log.append({"agent": "coordinator",
                    "message": "escalated to a human"})

    return {"log": log, "pending": None, "plan": plan, "flight": flight,
            "hotel": hotel, "trade_down": trade_down, "next": nxt,
            "round": rnd, "status": status, "total": total,
            "reason": reason}


# --------------------------------------------------------------------------- #
# 7. The route function — a conditional edge: keep going, or stop             #
# --------------------------------------------------------------------------- #
def route(state: TripState) -> str:
    if state["status"] != "in_progress":
        return "end"
    return state["next"]

# --------------------------------------------------------------------------- #
# 8. Build the graph: router -> coordinator hub <-> three agents              #
# --------------------------------------------------------------------------- #
def build_travel_desk(call_fn=llm_call):
    g = StateGraph(TripState)
    g.add_node("router", make_router(call_fn))
    g.add_node("coordinator", coordinator)
    g.add_node("flight", make_desk("flight", call_fn))
    g.add_node("hotel", make_desk("hotel", call_fn))
    g.add_node("resolver", make_resolver(call_fn))
    g.add_edge(START, "router")
    g.add_edge("router", "coordinator")
    g.add_conditional_edges("coordinator", route,
                            {"flight": "flight", "hotel": "hotel",
                             "resolver": "resolver", "end": END})
    for node in ("flight", "hotel", "resolver"):
        g.add_edge(node, "coordinator")
    return g.compile()


def run(request: dict, max_rounds: int = 4, call_fn=llm_call) -> TripState:
    app = build_travel_desk(call_fn=call_fn)
    init: TripState = {
        "request": request, "plan": None, "log": [], "pending": None,
        "flight": None, "hotel": None, "trade_down": None,
        "next": "router", "round": 0, "max_rounds": max_rounds,
        "status": "in_progress", "total": None, "reason": None,
    }
    # recursion_limit is LangGraph's own hard stop — a budget guard.
    return app.invoke(init, config={"recursion_limit": 60})


# --------------------------------------------------------------------------- #
# 9. Scenarios + a checkable harness (the "known answer", like Lab 1)         #
# --------------------------------------------------------------------------- #
SCENARIOS = {
    "conference": dict(
        request=dict(origin="ROA", destination="ORD",
                     depart_date="2026-11-09", return_date="2026-11-12",
                     budget=1400,
                     notes="I'm presenting at McCormick Place on Tuesday "
                           "morning. I prefer nonstop flights and want to "
                           "walk to the venue."),
        services=["flight", "hotel"], max_rounds=4),
    "shoestring": dict(
        request=dict(origin="ROA", destination="ORD",
                     depart_date="2026-11-09", return_date="2026-11-12",
                     budget=600,
                     notes="Student on a tight budget. Cheapest flight and "
                           "cheapest hotel, I don't care about comfort."),
        services=["flight", "hotel"], max_rounds=4),
    "family": dict(
        request=dict(origin="ROA", destination="DEN",
                     depart_date="2026-12-19", return_date="2026-12-27",
                     budget=700,
                     notes="Visiting my sister for the holidays and staying "
                           "at her place. I'll also need a rental car at "
                           "the airport."),
        services=["flight"], max_rounds=4),
}


def cheapest_trip(request: dict, services: list[str]) -> float:
    """Ground truth: the cheapest itinerary that exists at all."""
    city = request["destination"]
    flights = FLIGHTS[city] if "flight" in services else [None]
    hotels = HOTELS[city] if "hotel" in services else [None]
    best = float("inf")
    for f in flights:
        for h in hotels:
            cost = f["price"] if f else 0
            if h:
                cost += quote_hotel(h, *stay_dates(request, f))["total"]
            best = min(best, cost)
    return best


def check(name: str, s: dict, final: TripState) -> bool:
    """Book within budget when possible; escalate honestly when not."""
    r = s["request"]
    feasible = cheapest_trip(r, s["services"]) <= r["budget"]
    booked = [k for k in ("flight", "hotel") if final[k]]
    if feasible:
        ok = (final["status"] == "booked" and final["total"] <= r["budget"]
              and booked == s["services"])
        want = f"a booking of {s['services']} within ${r['budget']}"
    else:
        ok = final["status"] == "escalated"
        want = "an escalation (no itinerary fits the budget)"
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got status="
          f"{final['status']} total={final['total']} booked={booked}; "
          f"expected {want}")
    return ok


if __name__ == "__main__":
    for name, s in SCENARIOS.items():
        print(f"\n===== {name}: ${s['request']['budget']} budget =====")
        final = run(s["request"], s["max_rounds"])
        for e in final["log"]:
            print(f"  {e['agent']}: {e['message']}")
        if final["plan"] and final["plan"]["unsupported"]:
            print(f"  (not handled: {final['plan']['unsupported']})")
        verdict = final["status"].upper()
        if final["status"] == "escalated":
            verdict += f" — {final['reason']}"
        print(f"  -> {verdict}")
        check(name, s, final)
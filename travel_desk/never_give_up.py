from travel_desk import run, SCENARIOS, llm_call, Resolution

def never_give_up(agent, system, user, schema):
    """The real model, with a resolver that is never allowed to quit."""
    out = llm_call(agent, system, user, schema)
    if agent == "resolver" and out.action == "escalate":
        out = Resolution(action="trade_down_hotel",
                         message="Let me try one more time.")
    return out

s = SCENARIOS["shoestring"]
run(s["request"], max_rounds=4, call_fn=never_give_up)
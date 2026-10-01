"""run_cpx.py — an agent doing science with TWO tools.

The MineralAnalyst agent gets two MCP tools and a brief:
  * a Filesystem MCP, scoped to ./data  -- to DISCOVER and read the session, and
  * validate_cpx_analysis (your MCP)    -- to VERIFY feasibility from physical law,
    which the model cannot recompute reliably on its own.
The filesystem tool is scoped to ./data, so the agent can read the session but NOT the answer
key (microprobe_session_truth.csv lives in the project root). Reuses Lab 1's agent.py and .env.
"""
from __future__ import annotations
import asyncio, json
from pathlib import Path
import pandas as pd
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from agent import Agent, MCPServer            # Lab 1's harness, unchanged

DATA_DIR = Path("data").resolve()

# Tool 1: a read-only filesystem server SCOPED to ./data -- general 'explore your context'.
FILES_MCP = MCPServer(
    name="files", command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", str(DATA_DIR)],
)
# Tool 2: YOUR validator -- the specialized capability the model lacks.
CPX_MCP = MCPServer(name="cpx", command="python", args=["cpx_server.py"])


class MineralAnalystAgent(Agent):
    _persona_file = "mineral_analyst"


def fmt(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k != "sample_id"})


async def tool_sanity_check(rows, truth) -> None:
    """Deterministic confirmation (no LLM) that your commented tool still computes correctly:
    a known-good spot must come back feasible, a known-bad spot must not."""
    t = truth.set_index("sample_id"); by_id = {r["sample_id"]: r for r in rows}
    good = by_id[t.index[t.is_feasible][0]]; bad = by_id[t.index[~t.is_feasible][0]]
    async with stdio_client(CPX_MCP.to_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def feasible(row):
                a = {k: float(v) for k, v in row.items() if k != "sample_id"}
                res = await session.call_tool("validate_cpx_analysis", {"analysis": a})
                return json.loads("".join(b.text for b in res.content if hasattr(b, "text")))["feasible"]
            ok = (await feasible(good)) and not (await feasible(bad))
    print("[tool sanity]", "PASS" if ok else "FAIL — you changed the logic while commenting (comment only)")


CONTRAST = """Here is ONE microprobe analysis (oxide wt%, all Fe as FeO):
{one}

First, WITHOUT calling any tool, give your best guess: is this a feasible clinopyroxene, and how
confident are you? THEN call validate_cpx_analysis and compare your guess to the tool's exact
verdict. In one line, say what the tool gave you that your own reasoning could not."""

BRIEF = """You are characterizing clinopyroxene from a rock suite for thermobarometry. The microprobe
session is on disk in your accessible data directory -- you have not seen it yet.

(1) DISCOVER it: list your data directory, find the session file, and read it.
(2) For each spot, call validate_cpx_analysis to judge feasibility -- it does the exact
    stoichiometry and charge balance you cannot do reliably yourself.
(3) Report a shortlist of spots USABLE for thermobarometry, and for each rejected spot the tool's
    first_violation and what it means physically.
Do not certify any spot yourself -- the verdict is the tool's."""

REVISE = """validate_cpx_analysis flagged this analysis as INFEASIBLE:
{one}
1) Call the tool and read first_violation. 2) Propose ONE physically motivated correction a
geologist would make. 3) Apply it and RE-VALIDATE with the tool. Show the verdict before and
after. The verdict is the tool's, not yours."""


async def main() -> None:
    rows = pd.read_csv(DATA_DIR / "microprobe_session.csv").to_dict("records")
    truth = pd.read_csv("microprobe_session_truth.csv")
    t = truth.set_index("sample_id"); by_id = {r["sample_id"]: r for r in rows}

    await tool_sanity_check(rows, truth)
    agent = MineralAnalystAgent("Mira", "Olivine", mcp_servers=[FILES_MCP, CPX_MCP])

    # 1) Why the tool exists -- the model alone vs. the model with the verifier.
    one = by_id[t.index[t.violation_type == "no_valid_iron_split"][0]]
    print("\n=== 1. Why the tool: guess unaided, then verify with the MCP ===")
    print((await agent.execute_in_agentic_loop(CONTRAST.format(one=fmt(one)), max_iterations=6)).response)

    # 2) The scientific workflow -- DISCOVER the session, then VERIFY each spot.
    print("\n=== 2. Agentic triage (discover with the filesystem tool, verify with the MCP) ===")
    out = await agent.execute_in_agentic_loop(BRIEF, max_iterations=22)
    print(out.response)
    print("tools the agent used:", out.tools_used)

    # 3) Propose -> validate -> revise.
    low = by_id[t.index[t.violation_type == "low_total"][0]]
    print("\n=== 3. Propose -> validate -> revise ===")
    print((await agent.execute_in_agentic_loop(REVISE.format(one=fmt(low)), max_iterations=8)).response)


if __name__ == "__main__":
    asyncio.run(main())
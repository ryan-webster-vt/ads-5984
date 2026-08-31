"""Autonomous style: we state ONLY the requirement. The agent decides
the approach, how to verify it, and when it is done."""
import asyncio

from agent import ExpertCoderAgent, PYTHON_MCP

TASK = """\
How many prime numbers are there between 1 and 1,000,000 inclusive?

Requirements:
- The result must be reproducible (the same every run).
- The result must be verifiable: convince me the answer is correct, do not
  just assert it.

I am not telling you how. You decide the approach and the verification.
You have a Python tool (run_python_code) available.
"""


async def main() -> None:
    agent = ExpertCoderAgent("Ada", "Lovelace", mcp_servers=[PYTHON_MCP])
    result = await agent.execute_in_agentic_loop(TASK)
    print("=== AUTONOMOUS RUN ===")
    print(result.response)
    print("\n--- metrics ---")
    print(f"iterations:  {result.iterations_used}")
    print(f"tool calls:  {result.tool_calls_made}")
    print(f"tools used:  {result.tools_used}")
    print(f"hit cap:     {result.hit_max_iterations}")


if __name__ == "__main__":
    asyncio.run(main())
"""Workflow style: WE dictate the method. The agent fills in the steps
but does not decide what to do — we did."""
import asyncio

from agent import ExpertCoderAgent, PYTHON_MCP

TASK = """\
Compute how many prime numbers there are between 1 and 1,000,000 inclusive.

Do it exactly this way:
1. Implement a Sieve of Eratosthenes in pure Python.
2. Write unit tests asserting: there are 25 primes below 100, 168 primes
   below 1000; 2, 3, 5, 7, and 7919 are prime; 1, 4, 9, and 1000 are not.
3. Run the tests using the Python tool. Only if ALL tests pass, compute and
   print the final count for 1..1,000,000.
4. Report the count and the test results.

Run all code with the run_python_code tool.
"""


async def main() -> None:
    agent = ExpertCoderAgent("Ada", "Lovelace", mcp_servers=[PYTHON_MCP])
    result = await agent.execute_in_agentic_loop(TASK)
    print("=== WORKFLOW RUN ===")
    print(result.response)
    print("\n--- metrics ---")
    print(f"iterations:  {result.iterations_used}")
    print(f"tool calls:  {result.tool_calls_made}")
    print(f"tools used:  {result.tools_used}")
    print(f"hit cap:     {result.hit_max_iterations}")


if __name__ == "__main__":
    asyncio.run(main())
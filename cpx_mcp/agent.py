"""A minimal but real coding agent: an LLM with a constitutional prompt,
running a think-act-observe loop over one MCP tool (a Python sandbox).

This is deliberately small. There is no RAG, no persistence, no framework —
just the agent loop, so you can see exactly what makes it 'agentic':
the model, not this code, decides what happens next.
"""
from __future__ import annotations  # allow modern annotation syntax on older Pythons

import contextlib  # used for AsyncExitStack (batched cleanup of MCP servers)
import json        # parse the JSON-string arguments the LLM sends with tool calls
import os          # read endpoint/key/model from the environment
import uuid        # generate default agent ids
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv               # populate os.environ from a .env file
from openai import OpenAI                    # OpenAI-compatible chat client (works with any provider)
from mcp import ClientSession, StdioServerParameters  # MCP client plumbing
from mcp.client.stdio import stdio_client    # launch an MCP server as a stdio subprocess

# Load OPEN_AI_ENDPOINT / OPEN_AI_API_KEY / OPEN_AI_MODEL from .env
load_dotenv()

# Persona (.md) files live next to this script.
PERSONA_DIR = Path(__file__).resolve().parent


@dataclass
class MCPServer:
    """How to launch one MCP server as a subprocess over stdio."""

    name: str          # human-readable identifier for this server
    command: str       # executable to run (e.g. "deno")
    args: list[str]    # command-line arguments passed to that executable

    def to_params(self) -> StdioServerParameters:
        # Convert to the MCP library's launch-configuration type for stdio_client().
        return StdioServerParameters(command=self.command, args=self.args)


# The Python sandbox MCP server: Pyodide running under Deno, no Docker.
# It exposes a single tool, `run_python_code`, taking one string argument
# `python_code`. This is the agent's only tool — and it is enough.
PYTHON_MCP = MCPServer(
    name="python",
    command="deno",
    args=[
        "run",
        "-N",                            # allow network access (to fetch the JSR package)
        "-R=node_modules",               # allow read access to node_modules
        "-W=node_modules",               # allow write access to node_modules
        "--node-modules-dir=auto",       # let Deno auto-manage a local node_modules dir
        "jsr:@pydantic/mcp-run-python",  # the server itself, pulled from the JSR registry
        "stdio",                         # transport mode: speak MCP over stdin/stdout
    ],
)


@dataclass
class AgentResult:
    """What the agent loop returns, plus the metrics that let you SEE the
    difference between workflow-style and autonomous runs."""

    response: str                                        # the agent's final natural-language answer
    iterations_used: int = 0                             # how many loop iterations were consumed
    tool_calls_made: int = 0                             # total number of tool invocations
    tools_used: list[str] = field(default_factory=list)  # distinct tool names that were invoked
    hit_max_iterations: bool = False                     # True if the loop was cut off by the cap


class Agent:
    """Base agent: constitutional prompt + an LLM + a think-act-observe loop."""

    # Subclasses set this to the basename of their persona .md file.
    _persona_file: Optional[str] = None

    def __init__(
        self,
        first_name: str,
        last_name: str,
        mcp_servers: Optional[list[MCPServer]] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        # Random UUID unless the caller supplies an id, so runs can be tracked.
        self.id = agent_id or str(uuid.uuid4())
        self.first_name = first_name
        self.last_name = last_name
        # Tool servers this agent may use; each is launched on demand in the loop.
        self.mcp_servers = mcp_servers or []
        # OpenAI-compatible client: endpoint/key/model all come from .env,
        # so the same code works against any compliant provider.
        self._client = OpenAI(
            base_url=os.environ["OPEN_AI_ENDPOINT"],
            api_key=os.environ["OPEN_AI_API_KEY"],
        )
        self._model = os.environ["OPEN_AI_MODEL"]
        # Persona text, lazily loaded on first access (see constitutional_prompt).
        self._constitution: Optional[str] = None

    @property
    def full_name(self) -> str:
        # Convenience for logging/display ("Ada Lovelace", not two fields).
        return f"{self.first_name} {self.last_name}"

    @property
    def constitutional_prompt(self) -> str:
        """The agent's behavioral spec, loaded once from its persona file."""
        if self._constitution is None:
            if self._persona_file is None:
                raise NotImplementedError(
                    f"{type(self).__name__} has no _persona_file."
                )
            # Lazy load + memoize: read the persona .md file once per agent.
            path = PERSONA_DIR / f"{self._persona_file}.md"
            self._constitution = path.read_text(encoding="utf-8")
        return self._constitution

    async def execute_in_agentic_loop(
        self,
        task: str,
        max_iterations: int = 12,
    ) -> AgentResult:
        """Run the agent until it returns a plain answer or hits the cap.

        Each iteration:
          1. Send history (+ tool catalog) to the LLM.
          2. If it requested tool calls, run them via MCP and feed results back.
          3. Otherwise, that turn IS the final answer.

        `max_iterations` is the termination criterion — without it, an agent
        can loop forever (the first anti-pattern in this course).
        """
        # Conversation history: the constitution as system prompt, then the task.
        messages: list[dict] = []
        if self._persona_file is not None:
            messages.append(
                {"role": "system", "content": self.constitutional_prompt}
            )
        messages.append({"role": "user", "content": task})

        # Run metrics, so callers can see HOW the agent behaved.
        total_tool_calls = 0
        tools_used: set[str] = set()

        async with contextlib.AsyncExitStack() as stack:
            # Spawn every MCP server and collect its tools.
            openai_tools: list[dict] = []  # the tool catalog, in OpenAI's function-calling schema
            tool_to_session: dict[str, ClientSession] = {}  # tool name -> session that owns it

            for server in self.mcp_servers:
                # Launch the server subprocess and open an MCP session over
                # its stdio pipes. The AsyncExitStack guarantees every server
                # and session is cleanly torn down when the loop finishes.
                read, write = await stack.enter_async_context(
                    stdio_client(server.to_params())
                )
                session = await stack.enter_async_context(
                    ClientSession(read, write)
                )
                await session.initialize()
                # Discover the server's tools and re-expose them in OpenAI's
                # function-calling schema, remembering which session owns each.
                for tool in (await session.list_tools()).tools:
                    tool_to_session[tool.name] = session
                    openai_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": tool.inputSchema,
                            },
                        }
                    )

            # The agent loop.
            for iteration in range(max_iterations):
                # THINK: send the full history + tool catalog to the LLM;
                # the model decides whether to answer or to call a tool.
                kwargs = {"tools": openai_tools} if openai_tools else {}
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    **kwargs,
                )
                choice = response.choices[0]  # we asked for a single completion

                # Record the model's turn verbatim (as plain dicts, since the
                # provider returns objects) so it stays in the conversation.
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": choice.message.content,
                }
                if choice.message.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ]
                messages.append(assistant_msg)

                # No tool calls -> this is the final answer.
                # Belt-and-suspenders: only treat the turn as final when BOTH
                # the finish reason says so AND no tool calls are present.
                if (
                    choice.finish_reason != "tool_calls"
                    or not choice.message.tool_calls
                ):
                    # Terminal OBSERVE: the model stopped asking for tools, so
                    # its message text is the answer. Report metrics alongside.
                    return AgentResult(
                        response=choice.message.content or "",
                        iterations_used=iteration + 1,
                        tool_calls_made=total_tool_calls,
                        tools_used=sorted(tools_used),
                        hit_max_iterations=False,
                    )

                # Execute each requested tool call and feed results back.
                # ACT + OBSERVE: dispatch each requested call to the MCP session
                # that owns the tool, then append its output as a `tool` message
                # so the model sees the result on the next iteration.
                for tc in choice.message.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)  # arguments arrive as a JSON string
                    total_tool_calls += 1
                    tools_used.add(name)

                    # Look up which spawned server provides this tool.
                    session = tool_to_session.get(name)
                    if session is None:
                        # Unknown tool: report the error back to the model
                        # instead of crashing, so it can recover next turn.
                        out = f"Error: tool {name!r} not found."
                    else:
                        # Run the tool inside its MCP server (the sandbox).
                        result = await session.call_tool(name, args)
                        # Flatten the MCP content blocks into plain text.
                        out = "\n".join(
                            block.text
                            for block in result.content
                            if hasattr(block, "text")
                        )
                    # Reply on the same thread: tool_call_id ties this result
                    # to the exact call it answers, as the API requires.
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": out,
                        }
                    )

        # Loop exited by exhausting the iteration budget.
        # The model never produced a tool-free answer, so report that honestly
        # rather than fabricating one — the caller may retry with a higher cap.
        return AgentResult(
            response="[max_iterations reached without a final answer]",
            iterations_used=max_iterations,
            tool_calls_made=total_tool_calls,
            tools_used=sorted(tools_used),
            hit_max_iterations=True,
        )


class ExpertCoderAgent(Agent):
    """A software engineer agent. Its standards come entirely from
    expert_coder.md — no stubs, no confabulated APIs, no silent assumptions."""

    # Everything (loop, MCP client, LLM wiring) is inherited from Agent;
    # the only difference from the base class is which persona file it loads.
    _persona_file = "expert_coder"
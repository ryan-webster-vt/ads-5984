# How `agent.py` Works — Beginner Notes

`agent.py` is a small but **real** coding agent. It connects a large language model (LLM) to a Python "sandbox" where the model can actually run code, and it lets the model decide what to do next, step by step, until it has an answer for you.

There is no framework, no database, no extra machinery — just the core "agent loop", so you can clearly see what makes software *agentic*.

---

## The Big Picture

Think of the agent like a helpful programmer working for you over chat:

1. **You give it a task** (e.g., "write a function that sorts a list").
2. **It thinks** about what to do.
3. **It acts** — if it needs to try something, it runs Python code in a sandbox.
4. **It observes** the result of that code.
5. It repeats *think → act → observe* until it's confident enough to give you a final answer.

The key idea: **the model, not the code, decides what happens next.** The Python file just provides the loop and the tools.

```
        ┌─────────────────────────────────────────┐
        │                                         │
        ▼                                         │
   THINK (ask the LLM)                           │
        │                                         │
   wants a tool? ── no ──► final answer (done!)  │
        │                                         │
       yes                                        │
        │                                         │
   ACT (run the tool) ──► OBSERVE (show result ───┘
                          back to the LLM)
```

---

## Setup at the Top of the File

Before any classes are defined, the file does some preparation:

- **`load_dotenv()`** — reads a `.env` file (if you have one) and loads secrets like `OPEN_AI_ENDPOINT`, `OPEN_AI_API_KEY`, and `OPEN_AI_MODEL` into the environment. This keeps passwords/keys out of the code.
- **`PERSONA_DIR`** — the folder where "persona" files (Markdown files describing how the agent should behave) live. It's set to the same folder as the script itself.

Useful imports to know about:

| Import | Why it's here |
|---|---|
| `json` | The LLM sends tool arguments as JSON *text*; we parse it into a dictionary. |
| `uuid` | Generates a random unique ID for each agent. |
| `OpenAI` (from `openai`) | The chat client used to talk to the LLM. Works with any OpenAI-compatible provider. |
| `ClientSession`, `StdioServerParameters`, `stdio_client` (from `mcp`) | Used to launch and talk to MCP servers (explained below). |
| `contextlib.AsyncExitStack` | A helper that guarantees all launched servers get cleaned up at the end. |

> **What is MCP?** MCP (Model Context Protocol) is a standard way for an agent to connect to external "tool servers". In this file, the tool server is a Python sandbox — a safe place where the agent can execute Python code.

---

## Class: `MCPServer`

```python
@dataclass
class MCPServer:
    name: str
    command: str
    args: list[str]
```

This is a simple **recipe for launching one MCP server** as a subprocess (a separate program started by this one).

- `name` — a human-friendly label (e.g., `"python"`).
- `command` — the program to run (e.g., `"deno"`, a JavaScript runtime).
- `args` — extra command-line options passed to that program.

A `@dataclass` is just a shortcut in Python for a class that mostly holds data — you don't have to write an `__init__` yourself.

### Method: `to_params()`

Converts this recipe into `StdioServerParameters`, which is the object type the MCP library expects when launching a server. It's a translation step between "our simple format" and "the library's format".

---

## Constant: `PYTHON_MCP`

```python
PYTHON_MCP = MCPServer(name="python", command="deno", args=[...])
```

This is the **one and only tool** the agent has: a Python sandbox.

- It runs under **Deno** (no Docker needed) using **Pyodide** (Python compiled to run in the browser/JS world).
- It exposes a single tool called `run_python_code`, which takes one string of Python code and runs it.
- The flags like `-N` and `-R=node_modules` grant the sandbox the network and file permissions it needs to install itself.

The file's comment says it best: *this is the agent's only tool — and it is enough.*

---

## Class: `AgentResult`

```python
@dataclass
class AgentResult:
    response: str
    iterations_used: int = 0
    tool_calls_made: int = 0
    tools_used: list[str] = field(default_factory=list)
    hit_max_iterations: bool = False
```

This is **what the agent gives back when it finishes**. Besides the answer, it includes stats so you can *see* how the agent behaved:

| Field | Meaning |
|---|---|
| `response` | The agent's final plain-English answer. |
| `iterations_used` | How many laps around the loop it took. |
| `tool_calls_made` | Total number of tool invocations. |
| `tools_used` | Names of the *distinct* tools it used. |
| `hit_max_iterations` | `True` if it was cut off by the iteration limit (i.e., it never finished on its own). |

---

## Class: `Agent` (the heart of the file)

This is the base agent. It has three ingredients:

1. A **constitutional prompt** (a "persona" file describing its behavior),
2. an **LLM** (the brain),
3. and the **think-act-observe loop**.

### `__init__(first_name, last_name, mcp_servers=None, agent_id=None)`

The constructor. It stores:

- `self.id` — a random UUID (or one you provide) so runs can be tracked.
- `self.first_name` / `self.last_name` — a fun, human identity for the agent.
- `self.mcp_servers` — the list of tool servers it's allowed to use.
- `self._client` — an OpenAI client built from the endpoint/key in your `.env`, so the same code works with any compatible provider.
- `self._model` — which model to use, also from `.env`.
- `self._constitution` — starts as `None`; the persona text gets loaded lazily (see below).

### Property: `full_name`

A tiny convenience that joins the first and last name, e.g., `"Ada Lovelace"`.

### Property: `constitutional_prompt`

The agent's **behavioral spec** — a set of rules loaded from a Markdown file.

- If the persona hasn't been loaded yet (`self._constitution is None`), it reads the file `PERSONA_DIR / <persona_file>.md` **once** and remembers it (this is called *memoizing*, or lazy loading).
- If a subclass never set a `_persona_file`, it raises `NotImplementedError` to tell you something is misconfigured.

### Method: `execute_in_agentic_loop(task, max_iterations=12)` — the main event

This runs the agent until it produces a plain answer **or** runs out of iterations. Here's the walkthrough:

**Setup:**

1. Build the conversation `messages` list:
   - First, the persona/constitution as the `system` message (the agent's rules).
   - Then, your `task` as the `user` message.
2. Start counters for metrics (`total_tool_calls`, `tools_used`).
3. Open an `AsyncExitStack` and, for **each** MCP server in `self.mcp_servers`:
   - Launch the server as a subprocess (`stdio_client(server.to_params())`).
   - Open a `ClientSession` to talk to it, and call `session.initialize()`.
   - Ask the server what tools it offers (`session.list_tools()`), then:
     - Remember which session owns which tool (`tool_to_session`).
     - Translate each tool into OpenAI's function-calling format (`openai_tools`) so the LLM knows what it can call.

   The `AsyncExitStack` guarantees every server is shut down cleanly when the loop ends, even if something goes wrong.

**The loop** (repeats up to `max_iterations` times):

1. **THINK** — Send the entire conversation history plus the tool catalog to the LLM. The model decides: answer now, or call a tool?
2. Record the model's reply in the history. If it requested tool calls, those are saved too (converted to plain dictionaries).
3. **Is it done?** If the model did *not* ask for tool calls, its message **is** the final answer. Return an `AgentResult` with the answer and the stats.
4. **ACT + OBSERVE** — For each tool call the model requested:
   - Parse the JSON-string arguments into a dictionary (`json.loads`).
   - Look up which server provides that tool.
   - If the tool doesn't exist, send an error message *back to the model* instead of crashing, so it can recover next turn.
   - Otherwise run it with `session.call_tool(name, args)`, and flatten the result's content blocks into plain text.
   - Append the result to the history as a `tool` message, tied to the exact call it answers via `tool_call_id` (the API requires this pairing).

**After the loop:**

If the iterations run out and the model never gave a tool-free answer, the method returns an honest result: `"[max_iterations reached without a final answer]"` with `hit_max_iterations=True`. It does **not** invent a fake answer.

> **Why `max_iterations`?** Without a cap, an agent can loop forever — asking for tool call after tool call. This is the first anti-pattern the course warns about.

---

## Class: `ExpertCoderAgent`

```python
class ExpertCoderAgent(Agent):
    _persona_file = "expert_coder"
```

A **software engineer** agent. It inherits *everything* from `Agent` — the loop, the MCP client, the LLM wiring — and changes exactly one thing: which persona file it loads (`expert_coder.md`).

That Markdown file is where its professional standards live: no stub code, no made-up APIs, no silent assumptions. This shows a neat pattern: **you can create new agent personalities just by writing a new `.md` file and a two-line subclass.**

---

## Key Takeaways

1. **An agent = LLM + tools + a loop.** The magic isn't in fancy code; it's in letting the model choose its next action.
2. **The constitution (persona file) shapes behavior.** Same loop, different rules, different agent.
3. **Tools run out-of-process via MCP**, so the agent can safely execute code in a sandbox.
4. **Always set a termination condition** (`max_iterations`) so the loop can't run forever.
5. **Report metrics, not just answers.** `AgentResult` shows *how* the agent behaved, which makes agent runs debuggable and comparable.
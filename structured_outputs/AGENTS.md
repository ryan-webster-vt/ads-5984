# AGENTS.md — structured_outputs/

## What this is

ADS 5984 (Agentic AI in Practice), Lab 2 — Structured Outputs. The goal: send
free-form text to the VT ARC LLM and get back a strongly-typed Python object
using the OpenAI structured-outputs feature (`response_format=<PydanticModel>`),
with validation and automatic retries. No agent loop, no tools — just the
"model output → typed data" half of the model/program boundary.

## Session state — read PROGRESS.md first

`PROGRESS.md` at the repo root is the single source of truth for in-progress
task state. If it exists, read it before starting any work and resume from
its "Resume here" section. Keep it updated at important junctions (see the
progress-management skill).

## Files

- `extract.py` — the completed worked example and reference implementation.
  Defines `Ingredient`/`Recipe` Pydantic models and a generic
  `extract(text, schema: Type[T]) -> T` (TypeVar-bound) that:
  - calls `client.chat.completions.parse(..., response_format=schema, temperature=0)`
  - handles refusals (`message.refusal`), missing `.parsed`, `ValidationError`,
    and network/API errors
  - retries up to `max_retries=3` with linear backoff (`time.sleep(2 * attempt)`)
  - falls back to a non-strict `json_schema` request if strict-mode output is mangled
- `movie.py` — the lab exercise, currently EMPTY and to be implemented (the lab
  handout calls this file `movies.py`; `movie.py` is the local name).
- `.env` — secrets/config. Defines `OPEN_AI_ENDPOINT`, `OPEN_AI_API_KEY`,
  `OPEN_AI_MODEL` (the model id is `GLM-5.3`, exact spelling, hyphen not space).
  Loaded via `load_dotenv()`.
- `21 Lab 2 — Structured Outputs...pdf` — the assignment handout (specs below).

## Environment & commands

- Use the `ads_5984` conda environment (`conda activate ads_5984`).
- Dependencies: `openai`, `pydantic`, `python-dotenv` (verify with
  `python -c "import openai, pydantic, dotenv; print('deps OK')"`).
- Run the worked example: `python extract.py`
- Run the exercise: `python movie.py`
- The endpoint is only reachable on eduroam or the Cisco VPN.

## movie.py exercise spec (from the handout)

Extract a `Movie` with its cast from a free-form prose paragraph, reusing the
pattern from `extract.py`:

- Pydantic models matching this UML exactly:
  - `Movie`: `title: str`, `year: int`, `runtime_minutes: int`,
    `genres: list[str]`, `director: str`, `cast: list[CastMember]`,
    `rating: float`
  - `CastMember`: `actor: str`, `character: str`
- Reuse the generic extractor — easiest and best is `from extract import extract`
  (that reuse is the lesson of the lab).
- Write your own `MOVIE_TEXT`: a plain-prose paragraph about a real film,
  including title, year, runtime, genres, director, a few cast members with
  their characters, and a rating.
- Print the resulting `Movie` object, then iterate over `movie.cast` printing
  `actor → character`.
- Stretch goals (optional): `tagline: Optional[str]` and
  `box_office_millions: float | None` (model should leave them `None` when
  absent, not invent values); constrain `rating` to 0–10 with
  `Annotated[float, Field(ge=0, le=10)]` or a `field_validator` and watch the
  retry loop fire on out-of-range input.

## Patterns to follow (match extract.py exactly)

- Pydantic `BaseModel` classes ARE the schema; push variation into the schema
  and keep the call site generic.
- Always `temperature=0` for extraction — same parse every run, not creativity.
- Constraint beats hope: pass `response_format=schema` rather than asking for
  JSON in prose.
- Never trust model/server output: validate, and retry on `ValidationError`,
  refusals, and API/network errors with backoff.
- Known VT ARC endpoint quirk: strict/json_schema mode can prepend a stray `{`
  line before the JSON body. `extract._validate_lenient` repairs this by
  validating from the second `{` onward. Keep this fallback path.
- Keep `SYSTEM_PROMPT` terse — it is billed on every call. Treat each LLM call
  as spent money/latency; don't add extra calls or fields without cause.

## Security

- NEVER commit `.env`, print, or otherwise expose `OPEN_AI_API_KEY`. If
  `git init`-ing this folder, add `.env` to `.gitignore` first.

## Verifying work

- `python extract.py` must print the parsed `Recipe` object (with `servings`
  as a real `int`), then the JSON round-trip.
- `python movie.py` (once implemented) must print the parsed `Movie` and the
  actor → character list.
"""Lab 2 — Structured Outputs.

Ask the VT ARC LLM to read free-form text and return a strongly-typed Python
object, using the OpenAI structured-outputs feature
(response_format=<PydanticModel>), with validation and automatic retries.

There is no agent loop and no tools here. This is the other half of
"the model talks to your program": getting model output into a shape your
code can rely on.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Type, TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

# Load OPEN_AI_ENDPOINT / OPEN_AI_API_KEY / OPEN_AI_MODEL from .env
load_dotenv()

client = OpenAI(
    base_url=os.environ["OPEN_AI_ENDPOINT"],
    api_key=os.environ["OPEN_AI_API_KEY"],
)
MODEL = os.environ["OPEN_AI_MODEL"]


# ---- 1. The target shape: plain Python classes (Pydantic models) ----
# These classes ARE the schema. The model is constrained to fill them in.
class Ingredient(BaseModel):
    name: str
    quantity: str  # keep it human, e.g. "1 1/2 cups"


class Recipe(BaseModel):
    name: str
    servings: int
    ingredients: list[Ingredient]
    steps: list[str]


SYSTEM_PROMPT = (
    "You are a precise information-extraction service. Read the user's text and "
    "return ONLY data that matches the requested schema. Do not invent fields or "
    "facts; if a value is genuinely absent, make the most faithful inference the "
    "text supports."
)

# A TypeVar lets one function work for ANY Pydantic schema (Recipe, Movie, ...).
T = TypeVar("T", bound=BaseModel)


def _validate_lenient(schema: Type[T], text: str) -> T:
    """Validate `text` against `schema`, repairing one known corruption.

    Real endpoints have real bugs: the VT ARC server intermittently prepends
    a stray '{' line in json_schema mode, so the body arrives as
    '{\\n{\\n  "name": ...}' — invalid JSON. If plain validation fails, drop
    everything up to the SECOND '{' and validate again. This is exactly the
    fault-tolerance discipline this lab teaches: never assume the model (or
    the server) returned what you asked for.
    """
    try:
        return schema.model_validate_json(text)
    except ValidationError:
        first = text.find("{")
        second = text.find("{", first + 1)
        if second == -1:
            raise
        return schema.model_validate_json(text[second:])


def extract(text: str, schema: Type[T], *, max_retries: int = 3) -> T:
    """Extract `text` into an instance of `schema` using structured outputs.

    `response_format=schema` is the structured-outputs feature: the server is
    asked to constrain generation to the schema, so the reply already maps onto
    the class. temperature=0 because we want the SAME parse every run, not
    creativity (Module 2: low temperature for structured / JSON output).

    The try/except gives us several layers of safety:
      - API/network errors (timeouts, 5xx, rate limits) -> retry with backoff
      - model refusals (it can decline instead of answering) -> retry
      - a server that mangles strict-mode output -> non-strict fallback call
      - validation errors (output that does not fit the schema) -> retry
    """
    last_error: Exception | None = None
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    for attempt in range(1, max_retries + 1):
        try:
            try:
                completion = client.chat.completions.parse(
                    model=MODEL,
                    temperature=0,
                    messages=messages,
                    response_format=schema,  # <-- the structured-outputs feature
                )
                message = completion.choices[0].message

                # The model may refuse rather than answer. Not a parse error.
                if getattr(message, "refusal", None):
                    raise ValueError(f"model refused: {message.refusal}")

                parsed = message.parsed
                if parsed is None:
                    # Fallback: if the server did not honor the schema feature,
                    # validate the raw JSON text ourselves. Same result, by hand.
                    parsed = _validate_lenient(schema, message.content or "")

            except ValidationError:
                # ENDPOINT QUIRK: .parse() requests the schema in *strict*
                # mode, and the VT ARC server frequently mangles strict-mode
                # output (a stray '{' before the JSON). The cure is the same
                # fault-tolerance idea this lab teaches: fall back to a plain
                # (non-strict) json_schema request and validate ourselves.
                resp = client.chat.completions.create(
                    model=MODEL,
                    temperature=0,
                    messages=messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": schema.__name__,
                                        "schema": schema.model_json_schema()},
                    },
                )
                parsed = _validate_lenient(
                    schema, resp.choices[0].message.content or ""
                )

            return parsed  # success: a fully-typed Python object

        except (ValidationError, ValueError) as exc:
            last_error = exc
            print(f"[attempt {attempt}/{max_retries}] bad output: {exc}",
                  file=sys.stderr)
        except Exception as exc:  # network / API / rate-limit, etc.
            last_error = exc
            print(f"[attempt {attempt}/{max_retries}] API error: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

        if attempt < max_retries:
            time.sleep(2 * attempt)  # simple linear backoff

    raise RuntimeError(
        f"failed to extract {schema.__name__} after {max_retries} attempts"
    ) from last_error


# ---- 2. Some messy, free-form text to extract from ----
RECIPE_TEXT = """\
Grandma's buttermilk pancakes feed about four people. You'll need 1 1/2 cups of
all-purpose flour, 3 1/2 teaspoons of baking powder, 1 teaspoon of salt, 1
tablespoon of sugar, 1 1/4 cups of milk, 1 large egg, and 3 tablespoons of
melted butter. Whisk the dry ingredients together, make a well, and pour in the
milk, egg, and butter. Stir until just smooth. Heat a greased griddle over
medium and pour the batter by the 1/4 cup. Flip when bubbles form on the
surface, then cook until golden brown.
"""


def main() -> None:
    recipe = extract(RECIPE_TEXT, Recipe)

    # `recipe` is a real Python object, NOT a string of JSON. Prove it.
    print("=== Parsed Recipe object ===")
    print(repr(recipe))
    print()
    print(f"name:        {recipe.name}")
    print(f"servings:    {recipe.servings}  (type: {type(recipe.servings).__name__})")
    print(f"ingredients: {len(recipe.ingredients)}")
    for ing in recipe.ingredients:
        print(f"  - {ing.quantity} {ing.name}")
    print(f"steps:       {len(recipe.steps)}")
    for i, step in enumerate(recipe.steps, start=1):
        print(f"  {i}. {step}")

    # Because it is typed, you can compute on it directly — no string surgery.
    print("\n--- round-trip back to JSON ---")
    print(recipe.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
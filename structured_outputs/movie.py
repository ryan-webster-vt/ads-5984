"""Lab 2 — Structured Outputs, exercise: movies.

Do the worked example again, mostly from scratch, on a new schema: extract a
Movie (with its cast) from a paragraph of free-form prose, using structured
outputs, validation, and retries — then print the typed object.

The real lesson is reuse. extract.py already contains a GENERIC extractor,
extract(text, schema: Type[T]) -> T, that owns all the machinery: the
structured-outputs call (response_format=schema, temperature=0), refusal
handling, the mangled-strict-mode fallback, and the retry loop with backoff.
Every difference between "recipes" and "movies" lives in the Pydantic schema,
so this file only declares the new shape and supplies new text. The call site
is identical to extract.py's.
"""
from __future__ import annotations

from typing import Annotated, Optional

# Reusing the generic function instead of copy-pasting the retry loop is the
# point of the TypeVar design: the same call returns a Recipe in extract.py
# and a Movie here, with zero new parsing code. Importing extract also does
# all of this file's setup for free — it loads .env and builds the OpenAI
# client at import time — so no os/dotenv/OpenAI imports are needed here.
from extract import extract

from pydantic import BaseModel, Field


# ---- 1. The target shape, translated from the lab's UML diagram ----
# Movie:       str title, int year, int runtime_minutes, list<str> genres,
#              str director, list<CastMember> cast, float rating
# CastMember:  str actor, str character
#
# These classes ARE the schema: the model is constrained to fill them in,
# and Pydantic checks the types on the way back. That check is not a
# formality — the prose says "released in 1999" and "runs 136 minutes", and
# only validation guarantees year/runtime_minutes arrive as real ints and
# rating as a real float. A wrong or missing type is a ValidationError,
# which extract() treats like any other bad output: log, back off, retry.

# Defined before Movie because Movie's `cast` field refers to it (Pydantic
# resolves annotations at class-creation time, so definition order matters).
class CastMember(BaseModel):
    actor: str      # the person, e.g. "Keanu Reeves"
    character: str  # the role they play, e.g. "Neo"


class Movie(BaseModel):
    title: str
    year: int             # release year
    runtime_minutes: int  # the model converts "136 minutes" to this int
    genres: list[str]     # e.g. ["Action", "Sci-Fi"]
    director: str
    cast: list[CastMember]  # composition, exactly like Recipe -> Ingredient

    # Stretch goal: constrain rating to the sensible 0-10 range. The
    # Annotated form puts minimum/maximum into the JSON schema the server
    # is asked to honor AND has Pydantic re-check it after parsing, so an
    # out-of-range value (feed the model text claiming "87 out of 10" to
    # see it) fails validation and the retry loop fires.
    rating: Annotated[float, Field(ge=0, le=10)]

    # Stretch goals: fields the text may not mention. `Optional[str]` and
    # `str | None` are two spellings of the same thing — a nullable type.
    # A nullable field gives the model a lawful "absent" answer: when the
    # prose says nothing about a tagline or box-office take, it can return
    # null instead of inventing a value (the system prompt in extract.py
    # forbids fabrication; the schema makes "not present" representable).
    tagline: Optional[str] = None
    box_office_millions: float | None = None


# ---- 2. Some messy, free-form text to extract from ----
# Plain prose (NOT JSON) about a real film. It names every required field —
# title, year, runtime, genres, director, several actor/character pairs, and
# a rating — but deliberately says nothing about a tagline or box-office
# take, so the stretch-goal fields above should come back None, not invented.
MOVIE_TEXT = """\
The Matrix, released in 1999 and directed by Lana and Lilly Wachowski, is a
science-fiction action film that runs 136 minutes. Keanu Reeves stars as
Thomas Anderson, a hacker better known as Neo, who discovers that his world
is a simulation; Laurence Fishburne plays Morpheus, the rebel captain who
wakes him up to reality; Carrie-Anne Moss appears as Trinity, Morpheus's
cool-headed lieutenant; and Hugo Weaving is Agent Smith, the relentless
program hunting them. Reviewers give it around 8.7 out of 10.
"""


def main() -> None:
    # The same generic call as extract(RECIPE_TEXT, Recipe) in extract.py —
    # only the schema argument changed. The TypeVar means `movie` comes back
    # fully typed and validated as a Movie.
    movie = extract(MOVIE_TEXT, Movie)

    # `movie` is a real Python object, NOT a string of JSON. Prove it.
    print("=== Parsed Movie object ===")
    print(repr(movie))
    print()
    print(f"title:        {movie.title}")
    print(f"year:         {movie.year}  (type: {type(movie.year).__name__})")
    print(f"runtime:      {movie.runtime_minutes} minutes")
    print(f"genres:       {', '.join(movie.genres)}")
    print(f"director:     {movie.director}")
    print(f"rating:       {movie.rating}  (type: {type(movie.rating).__name__})")

    # Stretch-goal fields: MOVIE_TEXT mentions neither, so both should print
    # None. That is the schema and the system prompt cooperating — the model
    # had a lawful way to say "absent" and no license to fabricate.
    print(f"tagline:      {movie.tagline!r}")
    print(f"box_office:   {movie.box_office_millions!r}  (millions USD)")

    # Iterate over the cast, printing actor -> character for each member.
    print(f"cast:         {len(movie.cast)}")
    for member in movie.cast:
        print(f"  {member.actor} → {member.character}")

    # Because it is typed, you can compute on it directly — no string surgery.
    print("\n--- round-trip back to JSON ---")
    print(movie.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
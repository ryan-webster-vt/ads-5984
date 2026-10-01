# wiki_query.py — query an LLM Wiki with GLM-5.2. No embeddings, no vector DB.
# Retrieval is the index file: read wiki/index.md, let the model pick the relevant
# pages, open them, then answer from ONLY those pages with citations.
import os, re, json, glob
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

WIKI_DIR = "./wiki"
INDEX = os.path.join(WIKI_DIR, "index.md")

# One VT ARC client — GLM-5.2 does both page-selection and answering.
_arc = OpenAI(base_url=os.environ["OPEN_AI_ENDPOINT"], api_key=os.environ["OPEN_AI_API_KEY"])
_MODEL = os.environ["OPEN_AI_MODEL"]


def load_index() -> str:
    """The wiki's table of contents. This IS the retrieval layer."""
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _all_pages() -> list:
    """Every markdown page under wiki/, as paths relative to the repo root."""
    return sorted(glob.glob(os.path.join(WIKI_DIR, "**", "*.md"), recursive=True))


def select_pages(query: str, max_pages: int = 4) -> list:
    """Stage 1: hand GLM-5.2 the index and ask which pages to open.
    Returns up to max_pages file paths (relative to wiki/) that actually exist."""
    index_text = load_index()
    prompt = (
        "You are routing a question to pages of a markdown wiki. Below is the wiki's "
        "index (its table of contents). Choose the pages MOST likely to answer the "
        f"QUESTION — at most {max_pages}. Reply with ONLY a JSON array of page paths "
        "exactly as they appear in the index (relative to the wiki/ folder), e.g. "
        '["entities/nvidia.md", "sources/nvidia_10-K_FY2025.md"]. No other text.\n\n'
        f"INDEX:\n{index_text}\n\nQUESTION: {query}\n\nJSON array of page paths:"
    )
    resp = _arc.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=200,
    )
    text = resp.choices[0].message.content.strip()
    # Be robust: pull the first JSON array out of the reply, then keep only real files.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    names = json.loads(match.group()) if match else []
    picked = []
    for name in names:
        path = os.path.join(WIKI_DIR, name.lstrip("/"))
        if os.path.isfile(path):
            picked.append(path)
    return picked[:max_pages]


def read_pages(paths: list) -> list:
    """Return [(path, text), ...] for the selected pages."""
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            out.append((p, f.read()))
    return out


def generate_answer(query: str, max_pages: int = 4):
    """Full wiki query: select pages via the index, read them, answer from ONLY those
    pages with citations. Returns (answer_text, pages) where pages is [(path, text), ...]."""
    pages = read_pages(select_pages(query, max_pages))
    if not pages:
        return ("The wiki index did not point to any page for this question.", [])
    context = "\n\n".join(
        f"[{i + 1}] (page: {os.path.relpath(path, WIKI_DIR)})\n{text}"
        for i, (path, text) in enumerate(pages)
    )
    system = (
        "You answer questions from an LLM Wiki about NVIDIA's 10-K annual reports. "
        "Answer ONLY from the numbered wiki pages provided. Cite the pages you use as "
        "[1], [2], etc. If the pages do not contain the answer, say so plainly — do not guess."
    )
    user = f"Wiki pages:\n\n{context}\n\nQuestion: {query}"
    resp = _arc.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0,
    )
    return resp.choices[0].message.content, pages

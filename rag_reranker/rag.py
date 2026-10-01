# rag.py — retrieve from ChromaDB, re-rank with GLM-5.3 (from scratch), answer with GLM-5.3.
import os, re, time
from dotenv import load_dotenv
from openai import OpenAI
import chromadb
from build_index import OllamaEmbeddings, CHROMA_DIR, COLLECTION

load_dotenv()

# Reconnect to the persistent collection with the SAME embedding function used at ingest.
_client = chromadb.PersistentClient(path=CHROMA_DIR)
_collection = _client.get_collection(COLLECTION, embedding_function=OllamaEmbeddings())

# One VT ARC client, used for BOTH re-ranking and answering (GLM-5.3 does double duty).
_arc = OpenAI(base_url=os.environ["OPEN_AI_ENDPOINT"], api_key=os.environ["OPEN_AI_API_KEY"])
_CHAT_MODEL = os.environ["OPEN_AI_MODEL"]


def retrieve(query: str, k_in: int):
    """Stage 1: fast bi-encoder recall from ChromaDB. Returns k_in (text, metadata) candidates."""
    res = _collection.query(query_texts=[query], n_results=k_in)
    return list(zip(res["documents"][0], res["metadatas"][0]))


# ---- The re-ranker, from scratch ----
# A cross-encoder reads the query and ONE passage together and judges relevance.
# We do exactly that with an LLM: ask GLM-5.3 to rate, 0-10, how well a passage
# answers the query. This is "pointwise" LLM re-ranking — one model call per passage.
def _relevance_score(query: str, passage: str) -> float:
    prompt = (
        "You are scoring search results. Rate how well the PASSAGE answers the "
        "QUESTION on an integer scale from 0 to 10, where 10 means it directly and "
        "completely answers it and 0 means it is irrelevant. "
        "Reply with ONLY the integer, no words.\n\n"
        f"QUESTION: {query}\n\nPASSAGE:\n{passage}\n\nScore (0-10):"
    )
    # Two endpoint realities baked in here:
    #   1. GLM-5.3 is a REASONING model — it spends ~200 hidden reasoning
    #      tokens before emitting the integer, so a tiny max_tokens cap
    #      returns content=None. Give it headroom (512).
    #   2. VT ARC caps GLM-5.3 at ~30 requests/minute, and re-ranking makes
    #      k_in calls per question — so wait out a rate-limit window and
    #      retry instead of crashing.
    for _wait in range(20):
        try:
            resp = _arc.chat.completions.create(
                model=_CHAT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
            )
            break
        except Exception as exc:
            msg = str(exc)
            if "Rate limit" in msg:
                time.sleep(30)
                continue
            if "unavailable" in msg or "disconnected" in msg.lower():
                time.sleep(10)
                continue
            raise
    else:
        raise RuntimeError("rate-limited for too long; giving up")
    text = (resp.choices[0].message.content or "").strip()
    match = re.search(r"\d+", text)            # be robust if the model adds stray words
    return float(match.group()) if match else 0.0


def search(query: str, k_in: int = 10, k_out: int = 3):
    """Stage 1 + 2: retrieve k_in candidates, LLM-re-rank them, return the best k_out
    as (text, meta, score). NOTE: this makes k_in separate GLM-5.3 calls — one per
    candidate — so k_in directly sets the re-ranking cost and latency."""
    candidates = retrieve(query, k_in)
    scored = [(doc, meta, _relevance_score(query, doc)) for doc, meta in candidates]
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:k_out]


def generate_answer(query: str, k_in: int = 10, k_out: int = 3):
    """Full RAG: search, then have GLM-5.3 answer using ONLY the retrieved chunks.
    Returns (answer_text, hits) where hits is the list search() returned."""
    hits = search(query, k_in, k_out)
    context = "\n\n".join(
        f"[{i + 1}] (source: {m['source']}, {m['fiscal_year']})\n{doc}"
        for i, (doc, m, _score) in enumerate(hits)
    )
    system = (
        "You are a financial-analysis assistant answering questions about NVIDIA's "
        "10-K annual reports. Answer ONLY from the numbered context passages provided. "
        "Cite the passages you use as [1], [2], etc. If the context does not contain "
        "the answer, say so plainly — do not guess."
    )
    user = f"Context passages:\n\n{context}\n\nQuestion: {query}"
    # Same rate-limit patience as the re-ranker (~30 requests/min on VT ARC).
    for _wait in range(20):
        try:
            resp = _arc.chat.completions.create(
                model=_CHAT_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0,
            )
            break
        except Exception as exc:
            msg = str(exc)
            if "Rate limit" in msg:
                time.sleep(30)
                continue
            if "unavailable" in msg or "disconnected" in msg.lower():
                time.sleep(10)
                continue
            raise
    else:
        raise RuntimeError("rate-limited for too long; giving up")
    return resp.choices[0].message.content, hits
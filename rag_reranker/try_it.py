# try_it.py — compare plain retrieval vs. LLM-re-ranked retrieval on one question.
from rag import retrieve, search

Q = "How much data center revenue did NVIDIA report, and in which fiscal year?"
K_IN, K_OUT = 10, 3

print("=== Stage 1 only: top", K_OUT, "by vector similarity ===")
for doc, meta in retrieve(Q, K_IN)[:K_OUT]:
    print(f"  [{meta['fiscal_year']}] {doc[:90].strip()}...")

print("\n=== Stage 1 + 2: top", K_OUT, "after GLM-5.3 re-rank (0-10 relevance) ===")
for doc, meta, score in search(Q, K_IN, K_OUT):
    print(f"  [{meta['fiscal_year']}] score={score:4.1f}  {doc[:80].strip()}...")
    
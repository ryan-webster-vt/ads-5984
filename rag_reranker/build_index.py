# build_index.py — chunk the 10-K filings, embed with Ollama, store in ChromaDB.
import os, re, glob
from dotenv import load_dotenv
from openai import OpenAI
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

load_dotenv()

CHROMA_DIR = "./chroma_db"
COLLECTION = "nvidia_10"
DATA_GLOB  = "./data/*.md"


# ---- 1. Embedding function: call local Ollama via its OpenAI-compatible API ----
# ChromaDB will call this for every chunk on insert AND for every query, so the
# same model is guaranteed on both sides.
class OllamaEmbeddings(EmbeddingFunction):
    def __init__(self):
        # Ollama ignores the api_key, but the OpenAI client requires a non-empty string.
        self.client = OpenAI(base_url=os.environ["OLLAMA_ENDPOINT"], api_key="ollama")
        self.model = os.environ["EMBED_MODEL"]

    def __call__(self, input: Documents) -> Embeddings:
        resp = self.client.embeddings.create(model=self.model, input=list(input))
        return [d.embedding for d in resp.data]


# ---- 2. A simple, readable chunker ----
# Pack paragraphs up to ~1200 chars with a little overlap so a fact split across
# a boundary still survives in one chunk. Track the most recent Markdown heading
# as a "section" tag, and the fiscal year from the filename — both become metadata
# you can cite and filter on later.
def chunk_markdown(text, source, fiscal_year, max_chars=1200, overlap=150):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf, section = [], "", ""
    for p in paras:
        if p.lstrip().startswith("#"):
            section = p.lstrip("# ").strip()[:120]
        if buf and len(buf) + len(p) + 1 > max_chars:
            chunks.append((buf, section))
            buf = buf[-overlap:] + "\n" + p          # carry a little context forward
        else:
            buf = (buf + "\n" + p).strip()
    if buf.strip():
        chunks.append((buf, section))
    return [
        {"id": f"{source}::{i}",
         "text": c,
         "meta": {"source": source, "fiscal_year": fiscal_year, "section": sec or "n/a"}}
        for i, (c, sec) in enumerate(chunks)
    ]


# ---- 3. Build the collection ----
def main():
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    # Start clean so re-running the script is idempotent.
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    collection = client.create_collection(COLLECTION, embedding_function=OllamaEmbeddings())

    all_chunks = []
    for path in sorted(glob.glob(DATA_GLOB)):
        source = os.path.basename(path)
        fy = re.search(r"FY\d{4}", source)
        fy = fy.group(0) if fy else "unknown"
        text = open(path, encoding="utf-8").read()
        chunks = chunk_markdown(text, source, fy)
        all_chunks.extend(chunks)
        print(f"  {source}: {len(chunks)} chunks")

    print(f"Embedding {len(all_chunks)} chunks with Ollama (this calls the embed model)...")
    BATCH = 100
    for i in range(0, len(all_chunks), BATCH):
        batch = all_chunks[i:i + BATCH]
        collection.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["meta"] for c in batch],
        )
        print(f"    stored {i + len(batch)}/{len(all_chunks)}")

    print(f"Done. Collection '{COLLECTION}' now holds {collection.count()} chunks.")


if __name__ == "__main__":
    main()
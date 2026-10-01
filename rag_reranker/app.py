import streamlit as st
import rag

st.set_page_config(page_title="NVIDIA 10-K Chat", page_icon="📊")

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Retrieval settings")
    k_in = st.slider("k_in — candidates retrieved", min_value=3, max_value=20, value=10, step=1)
    k_out = st.slider("k_out — kept after re-ranking", min_value=1, max_value=8, value=3, step=1)
    if k_out > k_in:
        k_out = k_in
        st.caption(f"Clamped k_out to {k_out} so it does not exceed the {k_in} retrieved candidates.")
    st.caption(
        "k_in: how many chunks ChromaDB recalls; a larger k_in widens recall but means "
        "more re-ranking model calls (slower). k_out: how many survivors are kept; a "
        "smaller k_out tightens the context sent to the answering model."
    )

st.title("NVIDIA 10-K Chat")
st.caption("Ask questions about NVIDIA's 10-K filings — answers are grounded only in the retrieved passages.")


def render_sources(hits):
    with st.expander("Sources"):
        for i, (doc, meta, score) in enumerate(hits):
            st.markdown(
                f"**{meta['fiscal_year']}** — `{meta['source']}` — re-rank score **{score:g}/10**"
            )
            st.markdown(doc[:300] + ("…" if len(doc) > 300 else ""))
            if i < len(hits) - 1:
                st.divider()


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("hits"):
            render_sources(msg["hits"])

prompt = st.chat_input("Ask about NVIDIA's 10-Ks…")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner(f"Re-ranking {k_in} candidates (one GLM-5.3 call each — this can take several seconds)…"):
            answer, hits = rag.generate_answer(prompt, k_in, k_out)
        st.markdown(answer)
        render_sources(hits)
    st.session_state.messages.append({"role": "assistant", "content": answer, "hits": hits})
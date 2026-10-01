# app.py — Streamlit chat front-end over the existing wiki_query.py retrieval.
# No vector search: wiki_query reads wiki/index.md and opens up to max_pages pages.
import os

import streamlit as st

import wiki_query

st.set_page_config(page_title="NVIDIA 10-K Wiki", page_icon="📄")
st.title("NVIDIA 10-K Wiki")
st.caption("Ask questions about NVIDIA's FY2022–FY2026 10-K filings.")

with st.sidebar:
    max_pages = st.slider("wiki pages to open", min_value=2, max_value=8, value=4)
    st.caption(
        "There is no vector search in this wiki. The model reads `wiki/index.md` "
        "(the catalog), picks the most relevant pages, and opens up to "
        f"{max_pages} of them to answer from — so answers can only be as good "
        "as the index routing."
    )

if "history" not in st.session_state:
    st.session_state.history = []

for message in st.session_state.history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and message.get("pages"):
            with st.expander("Wiki pages opened"):
                for path, text in message["pages"]:
                    st.markdown(f"`{os.path.relpath(path, wiki_query.WIKI_DIR)}`")
                    st.text(text[:300])

if prompt := st.chat_input("Ask a question about NVIDIA's 10-Ks"):
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Reading the index and opening pages..."):
            answer, pages = wiki_query.generate_answer(prompt, max_pages)
        answer = answer or "The model returned no answer."
        st.markdown(answer)
        with st.expander("Wiki pages opened"):
            for path, text in pages:
                st.markdown(f"`{os.path.relpath(path, wiki_query.WIKI_DIR)}`")
                st.text(text[:300])

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "pages": pages}
    )

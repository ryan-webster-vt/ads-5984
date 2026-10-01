# try_it_wiki.py — see which pages the index routes to, and the grounded answer.
from wiki_query import select_pages, generate_answer

Q = "How did NVIDIA's data-center revenue change from FY2023 to FY2025?"

print("=== Pages the index routed to ===")
for path in select_pages(Q, max_pages=4):
    print("  ", path)

print("\n=== Grounded answer ===")
answer, pages = generate_answer(Q, max_pages=4)
print(answer)
print("\n(answered from", len(pages), "wiki pages)")
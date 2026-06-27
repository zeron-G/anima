"""DocumentStore on Postgres + pgvector (migrated off ChromaDB).

Embeddings are mocked to None in conftest, so chunks are stored without vectors
and search exercises the deterministic ILIKE fallback. The pgvector cosine path
shares pg_store's verified _vec_literal / `<=>` plumbing.
"""

import pytest

from anima.memory.document_store import DocumentStore


@pytest.mark.asyncio
async def test_document_roundtrip(pg_store, tmp_path):
    ds = DocumentStore(pg_store._db)

    f = tmp_path / "notes.txt"
    f.write_text(
        "Python is a programming language.\n\n"
        "Eva runs on a heartbeat loop.\n\n" * 4,
        encoding="utf-8",
    )

    res = await ds.import_document(str(f), description="test notes")
    assert res["success"] and res["chunks"] >= 1
    doc_id = res["doc_id"]

    docs = ds.list_documents()
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id
    assert docs[0]["filename"] == "notes.txt"
    assert docs[0]["chunks"] == res["chunks"]
    assert docs[0]["description"] == "test notes"

    # No OpenAI in tests → ILIKE fallback still finds the keyword.
    hits = await ds.search("heartbeat", n_results=5)
    assert hits and any("heartbeat" in h["content"] for h in hits)

    # doc_id filter scopes the search.
    scoped = await ds.search("Python", n_results=5, doc_id=doc_id)
    assert all(h["doc_id"] == doc_id for h in scoped)


@pytest.mark.asyncio
async def test_import_missing_file(pg_store, tmp_path):
    ds = DocumentStore(pg_store._db)
    res = await ds.import_document(str(tmp_path / "nope.txt"))
    assert not res["success"]


@pytest.mark.asyncio
async def test_delete_document(pg_store, tmp_path):
    ds = DocumentStore(pg_store._db)
    f = tmp_path / "doc.md"
    f.write_text("# Heading\n\nSome body text about Eva.", encoding="utf-8")
    res = await ds.import_document(str(f))
    doc_id = res["doc_id"]

    deleted = await ds.delete_document(doc_id)
    assert deleted["success"] and deleted["deleted"] == doc_id
    assert ds.list_documents() == []

    # Deleting again reports not-found.
    again = await ds.delete_document(doc_id)
    assert not again["success"]

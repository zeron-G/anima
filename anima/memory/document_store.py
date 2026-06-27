"""Document store — import, chunk, embed, and retrieve documents via pgvector.

Supports PDF, Markdown, and plain text files. Chunks are embedded with OpenAI
(text-embedding-3-small) and stored in the `documents` table alongside episodic
memory; semantic search is pgvector cosine. When OPENAI_API_KEY is unset, chunks
are stored without vectors and search falls back to keyword (ILIKE).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from anima.memory import embedder
from anima.memory.pg_db import PgDatabaseManager
from anima.memory.pg_store import _vec_literal
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("document_store")


class DocumentStore:
    """Import documents, chunk them, store in Postgres (pgvector) for RAG retrieval."""

    def __init__(self, db: PgDatabaseManager) -> None:
        self._db = db
        log.info("DocumentStore initialized (Postgres + pgvector)")

    async def import_document(
        self,
        file_path: str,
        description: str = "",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> dict:
        """Import a document: read -> chunk -> embed -> store in Postgres."""
        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        content = await asyncio.to_thread(self._read_file, path)
        if not content:
            return {"success": False, "error": "Could not read file or file is empty"}

        chunks = self._chunk_text(content, chunk_size, chunk_overlap)
        if not chunks:
            return {"success": False, "error": "No chunks generated"}

        doc_id = gen_id("doc")
        file_type = path.suffix.lstrip(".")
        now = time.time()
        embeddings = await embedder.embed_openai_batch(chunks)

        rows = []
        for i, chunk in enumerate(chunks):
            emb = embeddings[i] if embeddings else None
            rows.append((
                f"{doc_id}_c{i:04d}", doc_id, i, chunk, path.name, file_type,
                description, now, _vec_literal(emb) if emb else None,
            ))
        try:
            self._db.write_many_sync(
                "INSERT INTO documents (chunk_id, doc_id, chunk_index, content, "
                "filename, file_type, description, imported_at, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )
        except Exception as e:
            return {"success": False, "error": f"DB insert failed: {e}"}

        log.info("Imported document: %s (%d chunks)", path.name, len(chunks))
        return {
            "success": True,
            "doc_id": doc_id,
            "filename": path.name,
            "chunks": len(chunks),
        }

    async def search(
        self,
        query: str,
        n_results: int = 5,
        doc_id: str | None = None,
    ) -> list[dict]:
        """Semantic search across imported documents (pgvector cosine; ILIKE fallback)."""
        qvec = await embedder.embed_openai(query)
        try:
            if qvec:
                vlit = _vec_literal(qvec)
                clause = " AND doc_id=%s" if doc_id else ""
                params = (vlit, *((doc_id,) if doc_id else ()), vlit, n_results)
                rows = self._db.fetch_sync(
                    "SELECT chunk_id, doc_id, chunk_index, content, filename, "
                    "1-(embedding <=> %s::vector) AS relevance FROM documents "
                    "WHERE embedding IS NOT NULL" + clause +
                    " ORDER BY embedding <=> %s::vector LIMIT %s", params)
            else:
                clause = " AND doc_id=%s" if doc_id else ""
                params = (f"%{query}%", *((doc_id,) if doc_id else ()), n_results)
                rows = self._db.fetch_sync(
                    "SELECT chunk_id, doc_id, chunk_index, content, filename, "
                    "0.0 AS relevance FROM documents WHERE content ILIKE %s" + clause +
                    " LIMIT %s", params)
        except Exception as e:
            log.warning("Document search failed: %s", e)
            return []

        return [{
            "chunk_id": r["chunk_id"],
            "content": r["content"],
            "filename": r["filename"],
            "doc_id": r["doc_id"],
            "chunk_index": r["chunk_index"],
            "relevance": round(float(r["relevance"]), 3),
        } for r in rows]

    def list_documents(self) -> list[dict]:
        """List all imported documents (aggregated from their chunks)."""
        rows = self._db.fetch_sync(
            "SELECT doc_id, filename, file_type, description, "
            "MIN(imported_at) AS imported_at, COUNT(*) AS chunks FROM documents "
            "GROUP BY doc_id, filename, file_type, description "
            "ORDER BY MIN(imported_at) DESC")
        return [{
            "id": r["doc_id"],
            "filename": r["filename"],
            "file_type": r["file_type"],
            "description": r["description"],
            "chunks": r["chunks"],
            "imported_at": r["imported_at"],
        } for r in rows]

    async def delete_document(self, doc_id: str) -> dict:
        """Delete a document and all its chunks."""
        rows = self._db.fetch_sync(
            "SELECT COUNT(*) AS c FROM documents WHERE doc_id = %s", (doc_id,))
        if not rows or rows[0]["c"] == 0:
            return {"success": False, "error": f"Document {doc_id} not found"}
        self._db.write_sync("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
        return {"success": True, "deleted": doc_id}

    # -- File readers --

    def _read_file(self, path: Path) -> str:
        """Read file content based on extension."""
        ext = path.suffix.lower()
        try:
            if ext == ".pdf":
                return self._read_pdf(path)
            else:
                return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("Failed to read %s: %s", path, e)
            return ""

    @staticmethod
    def _read_pdf(path: Path) -> str:
        """Read PDF using pymupdf (fitz) if available, else skip."""
        try:
            import fitz  # pymupdf
            doc = fitz.open(str(path))
            text = "\n\n".join(page.get_text() for page in doc)
            doc.close()
            return text
        except ImportError:
            log.warning("pymupdf not installed — cannot read PDF. pip install pymupdf")
            return ""
        except Exception as e:
            log.warning("PDF read failed: %s", e)
            return ""

    # -- Chunking --

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
        """Split text into overlapping chunks respecting paragraph boundaries."""
        if not text.strip():
            return []

        paragraphs = text.split("\n\n")
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= chunk_size:
                current = (current + "\n\n" + para).strip()
            else:
                if current:
                    chunks.append(current)
                if len(para) > chunk_size:
                    # Split long paragraph by sentences
                    words = para.split()
                    current = ""
                    for word in words:
                        if len(current) + len(word) + 1 <= chunk_size:
                            current = (current + " " + word).strip()
                        else:
                            if current:
                                chunks.append(current)
                            current = word
                else:
                    current = para

        if current:
            chunks.append(current)

        return chunks

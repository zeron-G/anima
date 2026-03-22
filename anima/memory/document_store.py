"""Document store — import, chunk, embed, and retrieve documents via ChromaDB.

Supports PDF, Markdown, and plain text files. Uses ChromaDB for vector search
with the same PersistentClient as episodic memories (separate collection).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import chromadb

from anima.config import data_dir
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("document_store")


class DocumentStore:
    """Import documents, chunk them, store in ChromaDB for RAG retrieval."""

    def __init__(self, chroma_path: str | Path | None = None) -> None:
        path = Path(chroma_path) if chroma_path else data_dir() / "chroma"
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            "documents", metadata={"hnsw:space": "cosine"}
        )
        self._documents: dict[str, dict] = {}  # doc_id -> metadata
        log.info("DocumentStore initialized (collection: documents, path: %s)", path)

    async def import_document(
        self,
        file_path: str,
        description: str = "",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> dict:
        """Import a document: read -> chunk -> embed -> store in ChromaDB."""
        import asyncio

        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        # Read file content
        content = await asyncio.to_thread(self._read_file, path)
        if not content:
            return {"success": False, "error": "Could not read file or file is empty"}

        # Chunk
        chunks = self._chunk_text(content, chunk_size, chunk_overlap)
        if not chunks:
            return {"success": False, "error": "No chunks generated"}

        # Generate IDs
        doc_id = gen_id("doc")
        chunk_ids = [f"{doc_id}_c{i:04d}" for i in range(len(chunks))]

        # Store in ChromaDB
        try:
            self._collection.add(
                ids=chunk_ids,
                documents=chunks,
                metadatas=[{
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "filename": path.name,
                    "file_type": path.suffix.lstrip("."),
                    "description": description,
                } for i in range(len(chunks))],
            )
        except Exception as e:
            return {"success": False, "error": f"ChromaDB add failed: {e}"}

        # Track metadata
        self._documents[doc_id] = {
            "id": doc_id,
            "filename": path.name,
            "file_path": str(path),
            "file_type": path.suffix.lstrip("."),
            "chunks": len(chunks),
            "description": description,
            "imported_at": time.time(),
        }

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
        """Semantic search across imported documents."""
        import asyncio

        where_filter = {"doc_id": doc_id} if doc_id else None
        try:
            results = await asyncio.to_thread(
                self._collection.query,
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
            )
        except Exception as e:
            log.warning("Document search failed: %s", e)
            return []

        hits = []
        if results and results.get("ids") and results["ids"][0]:
            for i, chunk_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0
                hits.append({
                    "chunk_id": chunk_id,
                    "content": results["documents"][0][i] if results.get("documents") else "",
                    "filename": meta.get("filename", ""),
                    "doc_id": meta.get("doc_id", ""),
                    "chunk_index": meta.get("chunk_index", 0),
                    "relevance": round(1 - distance, 3),  # cosine distance -> similarity
                })
        return hits

    def list_documents(self) -> list[dict]:
        """List all imported documents."""
        return list(self._documents.values())

    async def delete_document(self, doc_id: str) -> dict:
        """Delete a document and all its chunks."""
        import asyncio
        if doc_id not in self._documents:
            return {"success": False, "error": f"Document {doc_id} not found"}

        try:
            # Get all chunk IDs for this document
            results = self._collection.get(where={"doc_id": doc_id})
            if results and results.get("ids"):
                await asyncio.to_thread(self._collection.delete, ids=results["ids"])
        except Exception as e:
            log.warning("Document delete failed: %s", e)
            return {"success": False, "error": str(e)}

        del self._documents[doc_id]
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

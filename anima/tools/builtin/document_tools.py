"""Document RAG tools — import, search, and manage documents."""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("document_tools")

# Module-level store reference (set from main.py)
_document_store = None


def set_document_store(store) -> None:
    global _document_store
    _document_store = store


async def _import_document(file_path: str, description: str = "") -> dict:
    if not _document_store:
        return {"success": False, "error": "Document store not initialized"}
    return await _document_store.import_document(file_path, description)


async def _search_documents(query: str, max_results: int = 5) -> dict:
    if not _document_store:
        return {"success": False, "error": "Document store not initialized"}
    results = await _document_store.search(query, n_results=max_results)
    return {"success": True, "results": results, "count": len(results)}


async def _list_documents() -> dict:
    if not _document_store:
        return {"success": False, "error": "Document store not initialized"}
    docs = _document_store.list_documents()
    return {"success": True, "documents": docs, "count": len(docs)}


async def _delete_document(doc_id: str) -> dict:
    if not _document_store:
        return {"success": False, "error": "Document store not initialized"}
    return await _document_store.delete_document(doc_id)


def get_document_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="import_document",
            description="Import a document (PDF/MD/TXT) for RAG search. Chunks and embeds it.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the document file"},
                    "description": {"type": "string", "description": "Brief description of the document"},
                },
                "required": ["file_path"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_import_document,
        ),
        ToolSpec(
            name="search_documents",
            description="Semantic search across imported documents",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["query"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_search_documents,
        ),
        ToolSpec(
            name="list_documents",
            description="List all imported documents",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_list_documents,
        ),
        ToolSpec(
            name="delete_document",
            description="Delete an imported document and its chunks",
            parameters={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Document ID to delete"},
                },
                "required": ["doc_id"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_delete_document,
        ),
    ]

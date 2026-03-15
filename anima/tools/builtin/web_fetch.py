"""Web fetch tool — HTTP GET to retrieve web content."""

from __future__ import annotations

import httpx

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _web_fetch(url: str, max_chars: int = 10000) -> str:
    """Fetch a URL and return its text content."""
    headers = {
        "User-Agent": "ANIMA/0.1 (autonomous AI agent)",
        "Accept": "text/html,application/json,text/plain,*/*",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

            if "json" in content_type:
                return resp.text[:max_chars]

            # For HTML, do a basic extraction of text content
            text = resp.text
            if "html" in content_type:
                text = _extract_text_from_html(text)

            if len(text) > max_chars:
                return text[:max_chars] + f"\n\n[truncated, total {len(text)} chars]"
            return text
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {str(e)[:200]}"
    except Exception as e:
        return f"Fetch error: {str(e)[:200]}"


def _extract_text_from_html(html: str) -> str:
    """Basic HTML to text — strip tags, decode entities."""
    import re
    # Remove script/style blocks
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    for entity, char in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                          ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def get_web_fetch_tool() -> ToolSpec:
    return ToolSpec(
        name="web_fetch",
        description="Fetch a URL and return its content as text. Works with web pages, APIs, and raw text files.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 10000)", "default": 10000},
            },
            "required": ["url"],
        },
        risk_level=RiskLevel.LOW,
        handler=_web_fetch,
    )

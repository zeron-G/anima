"""API Router — registers all /v1/* endpoints on the aiohttp app."""

from __future__ import annotations

from aiohttp import web

from anima.api import auth, chat, soulscape, evolution, memory, network, settings
from anima.utils.logging import get_logger

log = get_logger("api.router")


class APIRouter:
    """Registers all API routes on an aiohttp Application."""

    def __init__(self, hub) -> None:
        self._hub = hub

    def register(self, app: web.Application) -> None:
        """Register all /v1/* routes."""
        # Store hub reference for handlers
        app["hub"] = self._hub

        # Auth (no auth check needed for login itself)
        app.router.add_post("/v1/auth/login", auth.handle_login)
        app.router.add_post("/v1/auth/change-password", auth.handle_change_password)

        # Chat
        app.router.add_post("/v1/chat/send", chat.send)
        app.router.add_post("/v1/chat/stream", chat.stream)
        app.router.add_get("/v1/chat/history", chat.history)
        app.router.add_get("/v1/chat/sessions", chat.sessions)
        app.router.add_post("/v1/chat/golden", chat.golden)

        # Soulscape
        app.router.add_get("/v1/soulscape/emotion", soulscape.emotion)
        app.router.add_get("/v1/soulscape/persona", soulscape.persona)
        app.router.add_put("/v1/soulscape/persona", soulscape.update_persona)
        app.router.add_get("/v1/soulscape/personality", soulscape.personality)
        app.router.add_put("/v1/soulscape/personality", soulscape.update_personality)
        app.router.add_get("/v1/soulscape/relationship", soulscape.relationship)
        app.router.add_put("/v1/soulscape/relationship", soulscape.update_relationship)
        app.router.add_get("/v1/soulscape/growth-log", soulscape.growth_log)
        app.router.add_get("/v1/soulscape/golden-replies", soulscape.golden_replies)
        app.router.add_delete("/v1/soulscape/golden-replies/{id}", soulscape.delete_golden_reply)
        app.router.add_get("/v1/soulscape/style-rules", soulscape.style_rules)
        app.router.add_put("/v1/soulscape/style-rules", soulscape.update_style_rules)
        app.router.add_get("/v1/soulscape/boundaries", soulscape.boundaries)
        app.router.add_get("/v1/soulscape/drift", soulscape.drift)

        # Evolution
        app.router.add_get("/v1/evolution/status", evolution.status)
        app.router.add_get("/v1/evolution/history", evolution.history)
        app.router.add_get("/v1/evolution/governance", evolution.governance)
        app.router.add_put("/v1/evolution/governance/mode", evolution.update_governance_mode)

        # Memory
        app.router.add_get("/v1/memory/search", memory.search)
        app.router.add_get("/v1/memory/recent", memory.recent)
        app.router.add_get("/v1/memory/stats", memory.stats)
        app.router.add_get("/v1/memory/documents", memory.documents)
        app.router.add_post("/v1/memory/documents/import", memory.import_document)
        app.router.add_get("/v1/memory/documents/search", memory.search_documents)
        app.router.add_delete("/v1/memory/documents/{id}", memory.delete_document)

        # Network
        app.router.add_get("/v1/network/nodes", network.nodes)
        app.router.add_get("/v1/network/channels", network.channels)

        # Settings
        app.router.add_get("/v1/settings/config", settings.config_get)
        app.router.add_put("/v1/settings/config", settings.config_update)
        app.router.add_get("/v1/settings/skills", settings.skills)
        app.router.add_post("/v1/settings/skills/install", settings.install_skill)
        app.router.add_delete("/v1/settings/skills/{name}", settings.uninstall_skill)
        app.router.add_get("/v1/settings/system", settings.system_info)
        app.router.add_get("/v1/settings/usage", settings.usage)
        app.router.add_post("/v1/settings/restart", settings.restart)
        app.router.add_post("/v1/settings/shutdown", settings.shutdown)
        app.router.add_get("/v1/settings/traces", settings.traces)

        # TTS/STT (forward to existing handlers)
        # These will be wired from server.py's existing handlers

        log.info(
            "API routes registered: auth(2) + chat(5) + soulscape(14)"
            " + evolution(4) + memory(7) + network(2) + settings(10)"
        )

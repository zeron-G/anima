"""Mesh control-plane authorization.

Two planes (DISTRIBUTED_DESIGN §6.2-6.4):
  - DATA (gossip state, task_result, heartbeats, session, unknown events):
    PSK-HMAC membership is enough. Not gated here.
  - CONTROL (task_delegate / task_cancel / evolution_* / rollback / repair /
    quarantine): must carry a valid per-node Ed25519 signature AND the source's
    PINNED role must be authorized for the action.

Trust is OPERATOR-PINNED per node_id (pubkey + role) in `network.trust` — never
self-reported in gossip, because a compromised node could lie about its role.
Unknown / unsigned / unauthorized control messages are DROPPED (fail-closed).
"""
from __future__ import annotations

import time

from anima.utils.logging import get_logger

log = get_logger("network.authz")

# Control messages must be fresher than this. The signed body includes the
# timestamp, so this + per-authorizer id-dedup blocks replay of a captured
# control frame to a late-joining / partitioned node (DISTRIBUTED_DESIGN §7 #4).
CONTROL_MAX_AGE_S = 45.0

# Provenance keys the authorizer owns. Stripped from EVERY inbound message before
# authz so an attacker cannot pre-forge "I am trusted"; only tag_provenance re-adds
# them on a message it actually authorized. Absence ⇒ untrusted.
_PROV_KEYS = ("_mesh_src", "_mesh_role", "_mesh_trusted")


def strip_provenance(payload) -> None:
    """Remove any attacker-supplied provenance tags (recursively, incl. a nested
    `task` dict) so absence-of-tag reliably means 'not authorized by us'."""
    if isinstance(payload, dict):
        for k in _PROV_KEYS:
            payload.pop(k, None)
        inner = payload.get("task")
        if isinstance(inner, dict):
            for k in _PROV_KEYS:
                inner.pop(k, None)

# Roles
COORDINATOR = "coordinator"   # persistent server(s) — full authority
DEV = "dev"                   # developer/workstation node
EMBODIED = "embodied"         # robot / edge body — limited authority

# role → allowed actions ("evolve" from the design = propose + vote)
CAPABILITIES: dict[str, frozenset] = {
    COORDINATOR: frozenset({"delegate", "converse", "propose", "vote", "propagate",
                            "rollback", "repair", "quarantine", "beacon"}),
    DEV:         frozenset({"delegate", "converse", "propose", "vote",
                            "rollback", "repair", "beacon"}),
    EMBODIED:    frozenset({"delegate", "converse", "propose", "vote",
                            "self_quarantine", "beacon"}),
}

# event payload.type → control action. Inner event types NOT listed here are
# treated as data-plane (PSK membership only), e.g. session_release.
_EVENT_ACTION = {
    # node_discussion injects a tool-enabled USER_MESSAGE into the cognitive loop
    # — the single most dangerous sink — so it is CONTROL, not data (CRITICAL
    # finding #1: it was previously unclassified → ungated injection).
    "node_discussion": "converse",
    "evolution_propose": "propose",
    "evolution_vote": "vote",
    "evolution_deployed": "propagate",
    "evolution_promote": "propagate",
    "rollback": "rollback",
    "repair": "repair",
    "quarantine": "quarantine",       # refined to self_quarantine when target==source
    "defeated": "beacon",
    "health_beacon": "beacon",
    "canary_status": "beacon",
}
# top-level message.type → control action
_MSG_ACTION = {
    "task_delegate": "delegate",
    "task_cancel": "delegate",
}


class MeshAuthorizer:
    """Verifies + authorizes control-plane messages against pinned trust."""

    def __init__(self, trust: dict[str, dict] | None = None) -> None:
        # node_id → {"pubkey": hex, "role": role}
        self._trust = trust or {}
        # control message id → first-seen ts, for replay rejection within the
        # freshness window (independent of the gossip dedup cache, which can evict).
        self._seen_ctrl: dict[str, float] = {}

    @classmethod
    def from_config(cls) -> "MeshAuthorizer":
        from anima.config import get
        trust: dict[str, dict] = {}
        for entry in (get("network.trust", []) or []):
            nid = str(entry.get("node_id", "")).strip()
            role = str(entry.get("role", "")).strip().lower()
            pubkey = str(entry.get("pubkey", "")).strip()
            if nid and role in CAPABILITIES and pubkey:
                trust[nid] = {"pubkey": pubkey, "role": role}
            elif nid:
                log.warning("network.trust entry for %s ignored (bad role/pubkey)", nid)
        if trust:
            log.info("MeshAuthorizer: %d trusted peer(s): %s", len(trust),
                     ", ".join(f"{k}={v['role']}" for k, v in trust.items()))
        return cls(trust)

    # ── classification ──
    @staticmethod
    def action_for(msg) -> str | None:
        """The control action a message performs, or None if it's data-plane."""
        if msg.type in _MSG_ACTION:
            return _MSG_ACTION[msg.type]
        if msg.type == "event":
            inner = (msg.payload or {}).get("type", "")
            action = _EVENT_ACTION.get(inner)
            if action == "quarantine":
                # self-quarantine (target is the sender itself) is a lesser right
                tgt = (msg.payload or {}).get("node_id") or (msg.payload or {}).get("target")
                if tgt and tgt == msg.source_node:
                    return "self_quarantine"
            return action
        return None

    def is_control(self, msg) -> bool:
        return self.action_for(msg) is not None

    # ── verification + authorization ──
    def authorize(self, msg) -> tuple[bool, str]:
        """(ok, reason). Deny unless: source is a pinned trusted node, its Ed25519
        control signature verifies, and its role permits the action."""
        action = self.action_for(msg)
        if action is None:
            return True, "data-plane"          # not a control message

        entry = self._trust.get(msg.source_node)
        if not entry:
            return False, f"untrusted source {msg.source_node} (not in network.trust)"

        if not msg.verify_control(entry["pubkey"]):
            return False, f"bad/absent Ed25519 control signature from {msg.source_node}"

        allowed = CAPABILITIES.get(entry["role"], frozenset())
        if action not in allowed:
            return False, f"role '{entry['role']}' not permitted to '{action}'"

        # Freshness + replay: the signed body includes `timestamp`, so a stale or
        # replayed control frame is rejected even at a late-joining node (finding #4).
        now = time.time()
        ts = getattr(msg, "timestamp", 0) or 0
        if abs(now - ts) > CONTROL_MAX_AGE_S:
            return False, f"stale control message ({abs(now - ts):.0f}s old)"
        mid = getattr(msg, "id", "")
        if mid:
            if mid in self._seen_ctrl:
                return False, "replayed control message (id already seen)"
            # prune expired ids, then record this one
            if len(self._seen_ctrl) > 256:
                cutoff = now - CONTROL_MAX_AGE_S
                self._seen_ctrl = {k: v for k, v in self._seen_ctrl.items() if v > cutoff}
            self._seen_ctrl[mid] = now

        return True, "ok"

    def tag_provenance(self, msg) -> None:
        """Stamp the authenticated source + role onto the payload so downstream
        capability scoping (e.g. a delegated task's tool permissions) knows exactly
        which trusted peer/role this came from."""
        entry = self._trust.get(msg.source_node, {})
        if not isinstance(msg.payload, dict):
            return
        prov = {"_mesh_src": msg.source_node, "_mesh_role": entry.get("role", ""),
                "_mesh_trusted": True}
        msg.payload.update(prov)
        # Also stamp the nested task dict, because the task handler consumes
        # payload["task"] (not the outer payload) — otherwise its tags would be
        # whatever the sender put there (finding #3).
        inner = msg.payload.get("task")
        if isinstance(inner, dict):
            inner.update(prov)

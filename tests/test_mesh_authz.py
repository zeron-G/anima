"""Mesh control-plane trust layer (DISTRIBUTED_DESIGN §6): per-node Ed25519
signing + MeshAuthorizer capability matrix. These are the security-critical
authz checks — a compromised/unknown peer must not be able to issue control
commands (task_delegate / evolution_* / rollback / repair / quarantine)."""
from __future__ import annotations

from anima.network.keys import NodeKeys
from anima.network.protocol import NetworkMessage
from anima.network.authz import MeshAuthorizer, COORDINATOR, EMBODIED, DEV


def _keys(tmp_path, name):
    return NodeKeys.load_or_create(tmp_path / f"{name}.key")


def _authorizer(coord, pidog, dev):
    return MeshAuthorizer(trust={
        "azure": {"pubkey": coord.public_hex(), "role": COORDINATOR},
        "pidog": {"pubkey": pidog.public_hex(), "role": EMBODIED},
        "desk":  {"pubkey": dev.public_hex(),   "role": DEV},
    })


# ── Ed25519 sign/verify primitives ──
def test_control_sign_verify_roundtrip(tmp_path):
    k = _keys(tmp_path, "n")
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={"x": 1})
    m.sign_control(k)
    assert m.control_sig
    assert m.verify_control(k.public_hex()) is True
    # wrong key fails
    other = _keys(tmp_path, "other")
    assert m.verify_control(other.public_hex()) is False


def test_control_sig_detects_tamper(tmp_path):
    k = _keys(tmp_path, "n")
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={"cmd": "safe"})
    m.sign_control(k)
    m.payload["cmd"] = "rm -rf"          # tamper after signing
    assert m.verify_control(k.public_hex()) is False


def test_psk_and_control_sig_coexist(tmp_path):
    # Both signatures sign the SAME body (excluding both sig fields), so signing
    # one must not invalidate the other regardless of order.
    k = _keys(tmp_path, "n")
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={"x": 1})
    m.sign("psk-secret")
    m.sign_control(k)
    assert m.verify("psk-secret") is True
    assert m.verify_control(k.public_hex()) is True


# ── Authorization matrix ──
def test_data_plane_not_gated(tmp_path):
    a = _authorizer(_keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d"))
    for t in ("gossip", "task_result", "task_heartbeat"):
        m = NetworkMessage(type=t, source_node="azure")
        assert a.is_control(m) is False
        assert a.authorize(m)[0] is True


def test_untrusted_source_denied(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    atk = _keys(tmp_path, "atk")
    m = NetworkMessage(type="task_delegate", source_node="attacker", payload={})
    m.sign_control(atk)                 # validly signed, but attacker is not pinned
    ok, reason = a.authorize(m)
    assert ok is False and "untrusted" in reason


def test_trusted_coordinator_allowed(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={})
    m.sign_control(coord)
    assert a.authorize(m)[0] is True


def test_valid_role_but_unsigned_denied(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={})  # no control_sig
    ok, reason = a.authorize(m)
    assert ok is False and "signature" in reason


def test_impersonation_denied(tmp_path):
    # attacker claims to be azure but signs with its own key → azure's pinned
    # pubkey won't verify → denied (the core "compromised peer can't forge" property)
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    atk = _keys(tmp_path, "atk")
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={})
    m.sign_control(atk)
    assert a.authorize(m)[0] is False


def test_embodied_cannot_rollback_peer(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="event", source_node="pidog",
                       payload={"type": "rollback", "target": "azure"})
    m.sign_control(pidog)
    ok, reason = a.authorize(m)
    assert ok is False and "not permitted" in reason
    # coordinator CAN rollback
    m2 = NetworkMessage(type="event", source_node="azure",
                        payload={"type": "rollback", "target": "pidog"})
    m2.sign_control(coord)
    assert a.authorize(m2)[0] is True


def test_embodied_self_quarantine_ok_but_not_peer(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    # self-quarantine (target == self) allowed for embodied
    m_self = NetworkMessage(type="event", source_node="pidog",
                            payload={"type": "quarantine", "node_id": "pidog"})
    m_self.sign_control(pidog)
    assert a.authorize(m_self)[0] is True
    # quarantining a peer is not allowed for embodied
    m_peer = NetworkMessage(type="event", source_node="pidog",
                            payload={"type": "quarantine", "node_id": "azure"})
    m_peer.sign_control(pidog)
    assert a.authorize(m_peer)[0] is False


def test_tag_provenance(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={"task": "x"})
    a.tag_provenance(m)
    assert m.payload["_mesh_src"] == "azure"
    assert m.payload["_mesh_role"] == COORDINATOR
    assert m.payload["_mesh_trusted"] is True


def test_empty_trust_denies_all_control(tmp_path):
    # fail-closed: with no pinned peers, no control message is accepted
    a = MeshAuthorizer(trust={})
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={})
    m.sign_control(_keys(tmp_path, "c"))
    assert a.authorize(m)[0] is False


# ── Regression: ttl is decremented in transit and must NOT break the control sig
#    (the blocking defect the adversarial review found — every control msg was
#    dropped because ttl was in the signed body). ──
def test_ttl_decrement_preserves_control_sig(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={}, ttl=3)
    m.sign_control(coord)
    m.ttl -= 1                       # exactly what gossip recv does before the gate
    ok, reason = a.authorize(m)
    assert ok is True, reason        # would fail if ttl were in the signed body


# ── node_discussion is now CONTROL (was ungated cognitive-loop injection) ──
def test_node_discussion_is_gated_control(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="event", source_node="azure",
                       payload={"type": "node_discussion", "question": "hi"})
    assert a.is_control(m) is True
    # unsigned → denied (no ungated injection)
    assert a.authorize(m)[0] is False
    m.sign_control(coord)
    assert a.authorize(m)[0] is True


def test_unknown_event_is_not_control(tmp_path):
    # unclassified events are data-plane at the authz layer; main.py's dispatcher
    # fail-closes them (dropped, never injected).
    a = MeshAuthorizer(trust={})
    m = NetworkMessage(type="event", source_node="x", payload={"type": "weird", "text": "hax"})
    assert a.is_control(m) is False


# ── Replay + freshness (finding #4) ──
def test_replay_rejected(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={})
    m.sign_control(coord)
    assert a.authorize(m)[0] is True            # first time ok
    ok, reason = a.authorize(m)                 # same id replayed
    assert ok is False and "repl" in reason.lower()


def test_stale_control_rejected(tmp_path):
    import time
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure", payload={},
                       timestamp=time.time() - 300)   # 5 min old
    m.sign_control(coord)                              # signature is valid...
    ok, reason = a.authorize(m)                        # ...but it's stale
    assert ok is False and "stale" in reason


# ── Provenance strip (finding #3): attacker-preset tags removed on ingest ──
def test_strip_provenance_recursive():
    from anima.network.authz import strip_provenance
    payload = {"_mesh_trusted": True, "_mesh_role": "coordinator", "_mesh_src": "x",
               "task": {"_mesh_trusted": True, "cmd": "rm -rf"}}
    strip_provenance(payload)
    assert "_mesh_trusted" not in payload and "_mesh_role" not in payload
    assert "_mesh_trusted" not in payload["task"]
    assert payload["task"]["cmd"] == "rm -rf"          # non-prov keys preserved


def test_tag_provenance_stamps_nested_task(tmp_path):
    coord, pidog, dev = _keys(tmp_path, "c"), _keys(tmp_path, "p"), _keys(tmp_path, "d")
    a = _authorizer(coord, pidog, dev)
    m = NetworkMessage(type="task_delegate", source_node="azure",
                       payload={"task": {"cmd": "look"}})
    a.tag_provenance(m)
    assert m.payload["_mesh_trusted"] is True
    assert m.payload["task"]["_mesh_trusted"] is True   # handler reads payload["task"]

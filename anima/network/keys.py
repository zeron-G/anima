"""Per-node Ed25519 identity keys for the mesh CONTROL plane.

Every node has an Ed25519 keypair. The PRIVATE key never leaves the node (stored
0600 under data/.guardian/); the PUBLIC key is what peers pin in their
`network.trust` config to authenticate this node's dangerous control messages
(task_delegate, evolution_*, rollback, repair, quarantine).

This is asymmetric ON PURPOSE: with a shared symmetric secret, any verifier would
hold the signer's key and could forge its messages — so a compromised PiDog could
forge Azure's rollback command. With Ed25519, verifiers hold only PUBLIC keys and
cannot forge (DISTRIBUTED_DESIGN §6.2).
"""
from __future__ import annotations

import os
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("network.keys")


def _key_path() -> Path:
    from anima.config import data_dir
    d = data_dir() / ".guardian"
    d.mkdir(parents=True, exist_ok=True)
    return d / "node_ed25519.key"


class NodeKeys:
    """This node's Ed25519 signing identity for control-plane messages."""

    def __init__(self, private_key) -> None:
        self._priv = private_key

    def sign(self, data: bytes) -> bytes:
        return self._priv.sign(data)

    def public_hex(self) -> str:
        from cryptography.hazmat.primitives import serialization
        raw = self._priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return raw.hex()

    @classmethod
    def load_or_create(cls, path: Path | None = None) -> "NodeKeys":
        """Load this node's key from disk, generating (0600) on first run."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        p = path or _key_path()
        if p.exists():
            try:
                priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(p.read_text().strip()))
                return cls(priv)
            except Exception as e:  # noqa: BLE001 — corrupt key → regenerate
                log.warning("node ed25519 key unreadable (%s) — regenerating", e)
        priv = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization
        raw = priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        try:
            p.write_text(raw.hex())
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass  # best-effort on Windows
        except Exception as e:  # noqa: BLE001
            log.error("could not persist node ed25519 key: %s", e)
        obj = cls(priv)
        log.info("Generated node Ed25519 identity — PIN this pubkey in peers' "
                 "network.trust config: %s", obj.public_hex())
        return obj

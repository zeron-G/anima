"""Network protocol — message format, serialization, signing."""

import hashlib
import hmac as _hmac
import time
import msgpack
from dataclasses import dataclass, field, asdict
from anima.utils.ids import gen_id


@dataclass
class NetworkMessage:
    id: str = field(default_factory=lambda: gen_id("msg"))
    type: str = ""
    source_node: str = ""
    target_node: str = ""
    timestamp: float = field(default_factory=time.time)
    ttl: int = 3
    payload: dict = field(default_factory=dict)
    signature: str = ""          # PSK-HMAC — "a mesh member" (data plane)
    control_sig: str = ""        # per-node Ed25519 — "WHICH member" (control plane)

    def _body_bytes(self) -> bytes:
        """Serialize the message body for signing. Excludes BOTH signature fields
        (so neither covers the other) AND `ttl` — ttl is a hop counter mutated in
        transit (decremented on relay/recv), so signing it would make the signature
        fail after the first hop/decrement. id + type + source + target + timestamp
        + payload ARE covered (tamper-evident + dedup-bindable)."""
        d = asdict(self)
        d.pop("signature", None)
        d.pop("control_sig", None)
        d.pop("ttl", None)
        return msgpack.packb(d, use_bin_type=True)

    def pack(self) -> bytes:
        """Serialize the FULL message including signature."""
        return msgpack.packb(asdict(self), use_bin_type=True)

    @classmethod
    def unpack(cls, data: bytes) -> "NetworkMessage":
        """Deserialize from msgpack bytes."""
        d = msgpack.unpackb(data, raw=False)
        # L-26: validate required fields
        if not isinstance(d, dict) or "type" not in d:
            raise ValueError("Invalid network message: missing 'type' field")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def sign(self, secret: str) -> str:
        """Generate HMAC-SHA256 signature over the body (excluding signature field)."""
        self.signature = _hmac.new(
            secret.encode(), self._body_bytes(), hashlib.sha256
        ).hexdigest()[:32]
        return self.signature

    def verify(self, secret: str, max_age_s: float = 60.0) -> bool:
        """Verify message signature."""
        if not self.signature:
            return False
        expected = _hmac.new(
            secret.encode(), self._body_bytes(), hashlib.sha256
        ).hexdigest()[:32]
        if not _hmac.compare_digest(self.signature, expected):
            return False
        # Replay protection: reject messages outside the time window
        if max_age_s > 0 and abs(time.time() - self.timestamp) > max_age_s:
            return False
        return True

    # ── Ed25519 control-plane signature (per-node identity) ──
    def sign_control(self, private_key) -> str:
        """Sign the body with this node's Ed25519 private key. Proves WHICH node
        sent a dangerous control message — a compromised peer holding the shared
        PSK still cannot forge another node's signature."""
        self.control_sig = private_key.sign(self._body_bytes()).hex()
        return self.control_sig

    def verify_control(self, public_key_hex: str) -> bool:
        """Verify the Ed25519 control signature against a pinned public key."""
        if not self.control_sig or not public_key_hex:
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            try:
                pub.verify(bytes.fromhex(self.control_sig), self._body_bytes())
                return True
            except InvalidSignature:
                return False
        except Exception:  # noqa: BLE001 — malformed key/sig → not verified
            return False

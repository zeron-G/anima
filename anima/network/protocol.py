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
    ttl: int = 10
    payload: dict = field(default_factory=dict)
    signature: str = ""

    def _body_bytes(self) -> bytes:
        """Serialize the message body (excluding signature) for signing."""
        d = asdict(self)
        d.pop("signature", None)
        return msgpack.packb(d, use_bin_type=True)

    def pack(self) -> bytes:
        """Serialize the FULL message including signature."""
        return msgpack.packb(asdict(self), use_bin_type=True)

    @classmethod
    def unpack(cls, data: bytes) -> "NetworkMessage":
        """Deserialize from msgpack bytes."""
        d = msgpack.unpackb(data, raw=False)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def sign(self, secret: str) -> str:
        """Generate HMAC-SHA256 signature over the body (excluding signature field)."""
        self.signature = _hmac.new(
            secret.encode(), self._body_bytes(), hashlib.sha256
        ).hexdigest()[:32]
        return self.signature

    def verify(self, secret: str) -> bool:
        """Verify message signature."""
        if not self.signature:
            return False
        expected = _hmac.new(
            secret.encode(), self._body_bytes(), hashlib.sha256
        ).hexdigest()[:32]
        return _hmac.compare_digest(self.signature, expected)

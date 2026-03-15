"""Split-brain detection and readonly mode enforcement."""

from anima.network.node import NodeIdentity
from anima.utils.logging import get_logger

log = get_logger("network.split_brain")


class SplitBrainDetector:
    """Detects network partitions and enforces readonly mode for minority partitions."""

    def __init__(self, identity: NodeIdentity):
        self._identity = identity
        self._readonly = False

    @property
    def is_readonly(self) -> bool:
        return self._readonly

    def check(self, alive_count: int) -> bool:
        """Check if this node is in a minority partition.

        Args:
            alive_count: Number of nodes this node can see (including itself)

        Returns:
            True if in majority (normal operation), False if minority (readonly)
        """
        is_majority = self._identity.is_majority(alive_count)

        if not is_majority and not self._readonly:
            self._readonly = True
            log.warning(
                "SPLIT-BRAIN: minority partition detected (%d visible, %d registered). "
                "Entering readonly mode.",
                alive_count, self._identity.get_active_count(),
            )
        elif is_majority and self._readonly:
            self._readonly = False
            log.info("SPLIT-BRAIN: recovered to majority partition. Exiting readonly mode.")

        return is_majority

    def force_readonly(self, readonly: bool = True) -> None:
        """Manually set readonly mode (for maintenance)."""
        self._readonly = readonly
        log.info("Readonly mode %s (manual)", "enabled" if readonly else "disabled")

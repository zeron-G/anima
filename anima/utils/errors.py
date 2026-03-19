"""ANIMA unified exception hierarchy.

Every exception type corresponds to a clear recovery strategy.
Replaces scattered bare except, silent pass, and generic RuntimeError
throughout the codebase.

Hierarchy
---------
AnimaError
├── CommandRejected          — safety policy blocked a command
├── ToolExecutionError       — tool handler failed (timeout, crash, etc.)
├── LLMCallError             — LLM API call failed
├── MemoryCorruptionError    — database / memory integrity violation
├── EvolutionError           — evolution pipeline stage failure
├── ConfigurationError       — invalid config detected at startup
└── ContextTooSmallError     — prompt layers exceed context window
"""

from __future__ import annotations


class AnimaError(Exception):
    """Base class for all ANIMA exceptions."""


# ── Security ──


class CommandRejected(AnimaError):
    """A command was blocked by the safety policy.

    Raised by ``safe_subprocess.run_safe()`` and ``safety.assess_command_risk()``.
    The caller should return a structured error to the LLM, never retry
    the same command.
    """

    def __init__(self, message: str, *, command: str = "") -> None:
        self.command = command
        super().__init__(message)


class PathTraversalBlocked(AnimaError):
    """A file path resolved outside of its allowed root directory.

    Raised by ``path_safety.validate_path_within()``.
    """

    def __init__(self, path: str, allowed_root: str) -> None:
        self.path = path
        self.allowed_root = allowed_root
        super().__init__(
            f"Path traversal blocked: '{path}' is outside "
            f"allowed root '{allowed_root}'"
        )


# ── Tool Execution ──


class ToolExecutionError(AnimaError):
    """A tool handler failed during execution.

    Attributes
    ----------
    tool_name : str
        Name of the tool that failed.
    retryable : bool
        Whether the caller should retry the same invocation.
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        self.tool_name = tool_name
        self.retryable = retryable
        super().__init__(f"Tool '{tool_name}': {message}")


# ── LLM ──


class LLMCallError(AnimaError):
    """An LLM API call failed.

    Attributes
    ----------
    model : str
        Model identifier that was called.
    status_code : int
        HTTP status code (0 if not HTTP-related).
    retryable : bool
        True for transient errors (429, 500, 502, 503, 529).
    """

    _RETRYABLE_CODES = {429, 500, 502, 503, 529}

    def __init__(
        self,
        model: str,
        message: str,
        *,
        status_code: int = 0,
    ) -> None:
        self.model = model
        self.status_code = status_code
        self.retryable = status_code in self._RETRYABLE_CODES
        super().__init__(f"LLM '{model}' (HTTP {status_code}): {message}")


# ── Memory ──


class MemoryCorruptionError(AnimaError):
    """Memory data integrity violation detected.

    This is a serious error that may require administrator intervention
    (e.g. database repair or restore from backup).
    """


# ── Evolution ──


class EvolutionError(AnimaError):
    """Evolution pipeline failure.

    Attributes
    ----------
    stage : str
        Pipeline stage where the failure occurred
        (propose, consensus, implement, test, review, deploy).
    """

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"Evolution [{stage}]: {message}")


# ── Configuration ──


class ConfigurationError(AnimaError):
    """Invalid configuration detected.

    Raised at startup during config validation.  The process should
    exit immediately with a clear error message.
    """


class ContextTooSmallError(AnimaError):
    """The model's context window is too small to fit all required layers.

    Raised by ``TokenBudget`` when minimum allocations exceed the
    available context after reserving space for the response.
    """

    def __init__(self, deficit: int, available: int) -> None:
        self.deficit = deficit
        self.available = available
        super().__init__(
            f"Context window too small: need {available + deficit} tokens "
            f"for minimum allocations, but only {available} available"
        )

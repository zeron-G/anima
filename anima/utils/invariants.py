"""Runtime invariant checking — catch initialization and state bugs early.

Provides two mechanisms:
  1. require(condition, message) — assertion with logging
  2. @ensure_initialized(*attrs) — decorator that validates attributes are non-None

Usage:
    from anima.utils.invariants import require, ensure_initialized

    class MyService:
        @ensure_initialized("_db", "_router")
        async def process(self):
            # _db and _router are guaranteed non-None here
            ...

    def my_function(data):
        require(len(data) > 0, "data must not be empty")
"""

from __future__ import annotations

import functools
from typing import Callable, Any

from anima.utils.logging import get_logger

log = get_logger("invariants")


def require(condition: bool, message: str) -> None:
    """Runtime precondition check.

    Like assert, but:
      - Always active (not disabled by -O flag)
      - Logs the violation before raising
      - Provides clear error messages for debugging

    Args:
        condition: Must be True for execution to continue.
        message: Error message if condition is False.

    Raises:
        RuntimeError: If condition is False.
    """
    if not condition:
        log.error("Invariant violation: %s", message)
        raise RuntimeError(f"Invariant violation: {message}")


def ensure_initialized(*attrs: str) -> Callable:
    """Decorator: verify that specified attributes are non-None before method execution.

    Replaces scattered `if self._xxx is None: raise ...` checks with
    a declarative annotation.

    Args:
        *attrs: Attribute names that must be non-None on `self`.

    Usage:
        class Agent:
            @ensure_initialized("_prompt_compiler", "_memory_retriever")
            async def think(self):
                # _prompt_compiler and _memory_retriever guaranteed non-None
                ...
    """
    def decorator(method: Callable) -> Callable:
        @functools.wraps(method)
        async def async_wrapper(self, *args, **kwargs):
            for attr in attrs:
                val = getattr(self, attr, None)
                if val is None:
                    raise RuntimeError(
                        f"{self.__class__.__name__}.{method.__name__}() requires "
                        f"'{attr}' to be initialized (currently None). "
                        f"Check initialization order in main.py."
                    )
            return await method(self, *args, **kwargs)

        @functools.wraps(method)
        def sync_wrapper(self, *args, **kwargs):
            for attr in attrs:
                val = getattr(self, attr, None)
                if val is None:
                    raise RuntimeError(
                        f"{self.__class__.__name__}.{method.__name__}() requires "
                        f"'{attr}' to be initialized (currently None). "
                        f"Check initialization order in main.py."
                    )
            return method(self, *args, **kwargs)

        # Return the right wrapper based on whether method is async
        import asyncio
        if asyncio.iscoroutinefunction(method):
            return async_wrapper
        return sync_wrapper

    return decorator


def check_type(value: Any, expected_type: type, name: str = "value") -> None:
    """Runtime type check with clear error message.

    Args:
        value: The value to check.
        expected_type: Expected type (or tuple of types).
        name: Variable name for the error message.

    Raises:
        TypeError: If value is not of expected type.
    """
    if not isinstance(value, expected_type):
        raise TypeError(
            f"Expected {name} to be {expected_type.__name__}, "
            f"got {type(value).__name__}: {repr(value)[:100]}"
        )

"""Path safety utilities — prevent directory traversal attacks.

Every file I/O operation in ANIMA that involves user-controlled or
externally-sourced paths MUST validate the resolved path against an
allowed root directory using the functions in this module.

Protected surfaces:
  - ``evolution/sandbox.py`` — proposal file paths (H-16)
  - ``llm/lorebook.py`` — lorebook entry filenames (H-17)
  - ``tools/builtin/memory_tools.py`` — feelings/profile paths (H-18)
  - ``tools/builtin/file_ops.py`` — general file read/write
  - ``tools/builtin/edit.py`` — file editing
"""

from __future__ import annotations

from pathlib import Path

from anima.utils.errors import PathTraversalBlocked
from anima.utils.logging import get_logger

log = get_logger("path_safety")


def validate_path_within(path: Path, allowed_root: Path) -> Path:
    """Ensure *path* resolves to a location inside *allowed_root*.

    Parameters
    ----------
    path:
        The path to validate.  May be relative (resolved against cwd)
        or absolute.
    allowed_root:
        The directory that *path* must be contained within.

    Returns
    -------
    The resolved absolute path.

    Raises
    ------
    PathTraversalBlocked
        If *path* escapes *allowed_root* (including via symlinks).
    """
    resolved = path.resolve()
    root_resolved = allowed_root.resolve()

    # Primary check: is the resolved path under the root?
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PathTraversalBlocked(str(path), str(root_resolved))

    # Symlink check: if path itself is a symlink, verify the target
    # is also within root.  This catches symlinks that point outside
    # the allowed directory tree.
    if path.is_symlink():
        try:
            link_target = path.readlink().resolve()
            link_target.relative_to(root_resolved)
        except (ValueError, OSError):
            raise PathTraversalBlocked(
                f"{path} -> {path.readlink()}", str(root_resolved)
            )

    return resolved


def safe_project_path(relative: str) -> Path:
    """Resolve *relative* safely within the ANIMA project root.

    >>> safe_project_path("anima/core/cognitive.py")
    PosixPath('/path/to/anima/anima/core/cognitive.py')

    >>> safe_project_path("../../../etc/passwd")  # raises
    PathTraversalBlocked: ...
    """
    from anima.config import project_root

    return validate_path_within(project_root() / relative, project_root())


def safe_agent_path(relative: str) -> Path:
    """Resolve *relative* safely within the active agent directory.

    Used for feelings.md, identity files, lorebook entries, etc.
    """
    from anima.config import agent_dir

    return validate_path_within(agent_dir() / relative, agent_dir())


def safe_data_path(relative: str) -> Path:
    """Resolve *relative* safely within the data directory.

    Used for user_profile.md, notes, workspace files, etc.
    """
    from anima.config import data_dir

    return validate_path_within(data_dir() / relative, data_dir())


def is_safe_path(path: Path, allowed_root: Path) -> bool:
    """Non-raising version of ``validate_path_within``.

    Returns ``True`` if *path* is within *allowed_root*, ``False``
    otherwise.  Useful for filtering lists of paths without exception
    handling overhead.
    """
    try:
        validate_path_within(path, allowed_root)
        return True
    except PathTraversalBlocked:
        return False

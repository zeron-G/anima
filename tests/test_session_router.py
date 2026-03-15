"""Tests for session routing."""

import asyncio
import pytest
from anima.network.session_router import SessionRouter, Session


def test_lock_session():
    router = SessionRouter("node-a")
    assert router.try_lock("session-1", channel="terminal")
    assert router.is_mine("session-1")
    assert router.get_owner("session-1") == "node-a"


def test_lock_conflict_lower_wins():
    router = SessionRouter("node-b")
    router.try_lock("session-1")
    # Remote node with lower ID claims it
    router.handle_remote_lock("session-1", "node-a")
    # node-a should win (lower ID)
    assert router.get_owner("session-1") == "node-a"
    assert not router.is_mine("session-1")


def test_lock_conflict_higher_loses():
    router = SessionRouter("node-a")
    router.try_lock("session-1")
    # Remote node with higher ID tries to claim
    router.handle_remote_lock("session-1", "node-b")
    # node-a should keep it (lower ID)
    assert router.get_owner("session-1") == "node-a"


def test_release_session():
    router = SessionRouter("node-a")
    router.try_lock("session-1")
    router.release("session-1")
    assert not router.is_mine("session-1")
    assert router.get_owner("session-1") == ""


def test_release_dead_node_sessions():
    router = SessionRouter("node-a")
    # Simulate remote node owning sessions
    router.handle_remote_lock("s1", "node-b")
    router.handle_remote_lock("s2", "node-b")
    router.handle_remote_lock("s3", "node-c")

    released = router.release_all_for_node("node-b")
    assert len(released) == 2
    assert router.get_owner("s1") == ""
    assert router.get_owner("s2") == ""
    assert router.get_owner("s3") == "node-c"  # unaffected


def test_session_id_generation():
    router = SessionRouter("node-a")
    assert router.get_session_id("discord", "123") == "discord:123"
    assert router.get_session_id("terminal") == "terminal:default"


def test_cannot_lock_owned_by_other():
    router = SessionRouter("node-a")
    router.handle_remote_lock("session-1", "node-b", timestamp=__import__("time").time())
    assert not router.try_lock("session-1")


def test_list_sessions():
    router = SessionRouter("node-a")
    router.try_lock("s1", channel="terminal")
    router.try_lock("s2", channel="discord")
    sessions = router.list_sessions()
    assert len(sessions) == 2

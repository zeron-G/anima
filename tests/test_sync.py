"""Tests for memory sync and split-brain detection."""

import asyncio
import time
import pytest

from anima.network.sync import MemorySync
from anima.network.split_brain import SplitBrainDetector
from anima.network.node import NodeIdentity


# -- Sync tests --

def test_content_hash():
    h1 = MemorySync.content_hash("hello world", "chat")
    h2 = MemorySync.content_hash("hello world", "chat")
    h3 = MemorySync.content_hash("different", "chat")
    assert h1 == h2  # Same content = same hash
    assert h1 != h3  # Different content = different hash
    assert len(h1) == 16  # 16 hex chars


def test_next_seq():
    # Create a minimal MemorySync without actual DB
    sync = MemorySync.__new__(MemorySync)
    sync._local_seq = 0
    assert sync.next_seq() == 1
    assert sync.next_seq() == 2
    assert sync.next_seq() == 3


# -- Split-brain tests --

def test_split_brain_majority():
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
            {"id": "node-b", "status": "alive"},
            {"id": "node-c", "status": "alive"},
        ]
    }

    detector = SplitBrainDetector(identity)
    assert not detector.is_readonly

    # Can see 2 of 3 = majority
    assert detector.check(2) is True
    assert not detector.is_readonly


def test_split_brain_minority():
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
            {"id": "node-b", "status": "alive"},
            {"id": "node-c", "status": "alive"},
        ]
    }

    detector = SplitBrainDetector(identity)

    # Can only see self = minority
    assert detector.check(1) is False
    assert detector.is_readonly


def test_split_brain_recovery():
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
            {"id": "node-b", "status": "alive"},
        ]
    }

    detector = SplitBrainDetector(identity)

    # Minority
    detector.check(1)
    assert detector.is_readonly

    # Recovery -- see both nodes again
    detector.check(2)
    assert not detector.is_readonly


def test_split_brain_single_node():
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
        ]
    }

    detector = SplitBrainDetector(identity)
    # Single node is always majority
    assert detector.check(1) is True
    assert not detector.is_readonly

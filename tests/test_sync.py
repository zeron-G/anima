"""Tests for split-brain detection.

(Peer-to-peer MemorySync was removed with the SQLite backend — all nodes now
share one Postgres DB, so episodic replication between nodes is redundant.)
"""

import asyncio
import time
import pytest

from anima.network.split_brain import SplitBrainDetector
from anima.network.node import NodeIdentity


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

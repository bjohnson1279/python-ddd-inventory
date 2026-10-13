import pytest
from src.domain.crdt import PNCounter

def test_pn_counter_initialization():
    counter = PNCounter(node_id="node-1")
    assert counter.node_id == "node-1"
    assert counter.value() == 0
    assert counter.p == {}
    assert counter.n == {}

def test_pn_counter_increment_and_decrement():
    counter = PNCounter(node_id="node-1")
    counter.increment()
    assert counter.value() == 1

    counter.increment(5)
    assert counter.value() == 6

    counter.decrement()
    assert counter.value() == 5

    counter.decrement(3)
    assert counter.value() == 2

def test_pn_counter_merge_idempotency():
    # A merge A = A
    counter_a = PNCounter(node_id="node-1")
    counter_a.increment(10)
    counter_a.decrement(3)

    val_before = counter_a.value()
    p_before = dict(counter_a.p)
    n_before = dict(counter_a.n)

    counter_a.merge(counter_a)

    assert counter_a.value() == val_before
    assert counter_a.p == p_before
    assert counter_a.n == n_before

def test_pn_counter_merge_commutativity():
    # A merge B produces same resulting state as B merge A
    a1 = PNCounter(node_id="node-1")
    a1.increment(5)
    a1.decrement(2)

    a2 = PNCounter(node_id="node-2")
    a2.increment(8)
    a2.decrement(1)

    # State 1: A merged into B
    b_first = PNCounter(node_id="node-2")
    b_first.increment(8)
    b_first.decrement(1)
    b_first.merge(a1)

    # State 2: B merged into A
    a_first = PNCounter(node_id="node-1")
    a_first.increment(5)
    a_first.decrement(2)
    a_first.merge(a2)

    assert a_first.value() == b_first.value()
    assert a_first.p == b_first.p
    assert a_first.n == b_first.n

def test_pn_counter_merge_associativity():
    # (A merge B) merge C == A merge (B merge C)
    node_a = PNCounter("node-1")
    node_a.increment(10)

    node_b = PNCounter("node-2")
    node_b.decrement(4)

    node_c = PNCounter("node-3")
    node_c.increment(7)

    # Left grouping: (A merge B) merge C
    left = PNCounter("node-1")
    left.p = dict(node_a.p)
    left.n = dict(node_a.n)
    left._cached_value = node_a.value()
    left.merge(node_b)
    left.merge(node_c)

    # Right grouping: B merge C first, then A merge (BC)
    bc = PNCounter("node-2")
    bc.p = dict(node_b.p)
    bc.n = dict(node_b.n)
    bc._cached_value = node_b.value()
    bc.merge(node_c)

    right = PNCounter("node-1")
    right.p = dict(node_a.p)
    right.n = dict(node_a.n)
    right._cached_value = node_a.value()
    right.merge(bc)

    assert left.value() == right.value()
    assert left.p == right.p
    assert left.n == right.n

def test_pn_counter_concurrent_updates_merge():
    node1 = PNCounter("node-1")
    node2 = PNCounter("node-2")

    node1.increment(15)
    node1.decrement(5)

    node2.increment(20)
    node2.decrement(2)

    node1.merge(node2)
    assert node1.value() == (15 - 5) + (20 - 2)

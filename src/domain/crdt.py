from typing import Dict

class PNCounter:
    """Positive-Negative Counter CRDT for distributed stock counting."""
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.p: Dict[str, int] = {}
        self.n: Dict[str, int] = {}
        # Bolt Optimization: Maintain O(1) running total to avoid O(N) dict sum traversals on every value read
        self._cached_value = 0

    def increment(self, amount: int = 1):
        self.p[self.node_id] = self.p.get(self.node_id, 0) + amount
        self._cached_value += amount

    def decrement(self, amount: int = 1):
        self.n[self.node_id] = self.n.get(self.node_id, 0) + amount
        self._cached_value -= amount

    def value(self) -> int:
        # Return O(1) cached value instead of O(N) calculation
        return self._cached_value

    def merge(self, other: 'PNCounter'):
        for node, val in other.p.items():
            self.p[node] = max(self.p.get(node, 0), val)
        for node, val in other.n.items():
            self.n[node] = max(self.n.get(node, 0), val)
        # Recalculate cached value after bulk merge
        self._cached_value = sum(self.p.values()) - sum(self.n.values())

class ReplicationManager:
    """Multi-Region Active-Active Replication & Conflict Resolution."""
    def __init__(self):
        self.counters: Dict[str, PNCounter] = {}

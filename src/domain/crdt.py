from typing import Dict, Any

class PNCounter:
    """Positive-Negative Counter CRDT for distributed stock counting."""
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.p: Dict[str, int] = {}
        self.n: Dict[str, int] = {}

    def increment(self, amount: int = 1):
        self.p[self.node_id] = self.p.get(self.node_id, 0) + amount

    def decrement(self, amount: int = 1):
        self.n[self.node_id] = self.n.get(self.node_id, 0) + amount

    def value(self) -> int:
        return sum(self.p.values()) - sum(self.n.values())

    def merge(self, other: 'PNCounter'):
        for node, val in other.p.items():
            self.p[node] = max(self.p.get(node, 0), val)
        for node, val in other.n.items():
            self.n[node] = max(self.n.get(node, 0), val)

class ReplicationManager:
    """Multi-Region Active-Active Replication & Conflict Resolution."""
    def __init__(self):
        self.counters: Dict[str, PNCounter] = {}

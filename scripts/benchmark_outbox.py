import asyncio
import time
from typing import List, NamedTuple
from unittest.mock import AsyncMock, patch

class DummyEvent(NamedTuple):
    id: str
    aggregate_type: str
    aggregate_id: str

async def sequential_invalidation(events: List[DummyEvent], mock_invalidate):
    for event in events:
        await mock_invalidate(f"{event.aggregate_type}:{event.aggregate_id}")

async def parallel_invalidation(events: List[DummyEvent], mock_invalidate):
    # Performance Optimization: Collect unique cache keys and invalidate concurrently using asyncio.gather.
    # Deduplication eliminates redundant cache calls, and asyncio.gather eliminates sequential I/O round trips.
    cache_keys = {f"{event.aggregate_type}:{event.aggregate_id}" for event in events}
    if cache_keys:
        await asyncio.gather(*(mock_invalidate(key) for key in cache_keys))

async def run_benchmark():
    # Simulate 50 events in a batch (e.g., 10 unique aggregate IDs with 5 events each)
    events = [
        DummyEvent(
            id=str(i),
            aggregate_type="product",
            aggregate_id=f"prod_{i % 10}"
        )
        for i in range(50)
    ]

    # Simulated network latency per Redis delete call (e.g., 2ms)
    async def mock_redis_invalidate(key: str):
        await asyncio.sleep(0.002)

    # Measure sequential invalidation
    start_time = time.perf_counter()
    await sequential_invalidation(events, mock_redis_invalidate)
    seq_duration = time.perf_counter() - start_time

    # Measure parallel invalidation
    start_time = time.perf_counter()
    await parallel_invalidation(events, mock_redis_invalidate)
    par_duration = time.perf_counter() - start_time

    speedup = ((seq_duration - par_duration) / seq_duration) * 100
    ratio = seq_duration / par_duration if par_duration > 0 else 0

    print("=== Outbox Cache Invalidation Benchmark ===")
    print(f"Batch size: {len(events)} events (10 unique keys)")
    print(f"Sequential Duration: {seq_duration * 1000:.2f} ms")
    print(f"Parallel Duration:   {par_duration * 1000:.2f} ms")
    print(f"Latency Reduction:   {speedup:.2f}%")
    print(f"Speedup Ratio:       {ratio:.2f}x fast")

if __name__ == "__main__":
    asyncio.run(run_benchmark())

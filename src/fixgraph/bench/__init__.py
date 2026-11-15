"""Benchmark: does the graph improve where a vector store cannot?

Four arms, same episode history, same candidate fixes:
  no-memory, vector store, vector store + env filter, fixgraph.
Run:  python -m fixgraph.bench --tier synthetic --seeds 3
"""

__all__ = []

"""RSI Demo — trigger a full self-improvement cycle.

This demonstrates GBase's unique Recursive Self-Improvement.
The agent evaluates its own performance, detects failure patterns,
and proposes improvements to its system prompt.

Note: This example requires an active API key.

Usage:
    cd /path/to/gbase
    python3 examples/05_rsi_demo.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.evolution_engine import EvolutionEngine


async def rsi_cycle():
    engine = EvolutionEngine()
    print("🔄 Triggering RSI cycle...")
    result = await engine.full_evolution_cycle()
    print(f"\n✅ Score delta: {result.get('score_delta', 'N/A')}")
    print(f"📊 Before: {result.get('score_before', 'N/A')}")
    print(f"📊 After:  {result.get('score_after', 'N/A')}")

    if result.get("rollback"):
        print("↩️  Agent decided to rollback — changes weren't beneficial")
    else:
        print("🎉 Agent found genuine improvements!")

    print(f"\n🔬 Details: {result.get('details', '')}")


asyncio.run(rsi_cycle())

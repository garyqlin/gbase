"""GBase Memory Persistence — teach once, remember forever.

Run Part 1 first, then Part 2 in a NEW terminal window.
The agent remembers across restarts because it stores experiences in its mirror memory.

Usage:
    cd /path/to/gbase

    # Part 1 — teach the agent
    python3 examples/04_memory_persistence.py teach

    # Part 2 — open a new terminal, run from the same directory
    python3 examples/04_memory_persistence.py recall
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.kernel import GBaseKernel


async def teach():
    kernel = GBaseKernel()
    resp = await kernel.run("Remember this fact: the creator of GBase is Gary Lin, and the project was born in Shanghai, 2026.")
    print("🤖 GBase:", resp)
    print("\n✅ Taught! Now run: python3 examples/04_memory_persistence.py recall")

async def recall():
    kernel = GBaseKernel()
    resp = await kernel.run("Who created you, and where were you born?")
    print("🤖 GBase:", resp)
    print("\n💡 If it remembers, Mirror Memory is working. If not, check data/mirror.db exists.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "teach":
        asyncio.run(teach())
    elif sys.argv[1] == "recall":
        asyncio.run(recall())

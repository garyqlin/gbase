"""Chat with a GBase agent programmatically.

Run from the gbase directory:
    cd /path/to/gbase
    python3 examples/02_quick_chat.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.kernel import GBaseKernel


async def chat():
    kernel = GBaseKernel()

    response = await kernel.run("Hello! Who are you?")
    print("🤖 GBase:", response)

    response = await kernel.run("What did I just ask you?")
    print("🤖 GBase:", response)

    response = await kernel.run("Prove you remember: what was my first question?")
    print("🤖 GBase:", response)


asyncio.run(chat())

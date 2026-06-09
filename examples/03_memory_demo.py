"""GBase Memory Demo — show persistent cross-session memory.

Run it twice. The second time, it will remember our first conversation.
"""

# First conversation — the agent learns something
print("=== Session 1: Teaching ===")
import asyncio

from gbase.lib.kernel import GBaseKernel


async def teach():
    kernel = GBaseKernel()
    resp = await kernel.run("Remember this: my favorite color is ocean blue.")
    print("GBase:", resp)
    print("Memory saved! Now run this script again to see if it remembers.")


async def recall():
    kernel = GBaseKernel()
    resp = await kernel.run("What's my favorite color?")
    print("GBase:", resp)


if __name__ == "__main__":
    import sys

    if "--recall" in sys.argv:
        asyncio.run(recall())
    else:
        asyncio.run(teach())

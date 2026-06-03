"""Hello GBase — your first agent in 10 lines.

Steps:
    1. Clone: git clone https://github.com/garyqlin/gbase.git && cd gbase
    2. Configure: cp .env.example .env && edit .env with your API key
    3. Run:    python3 examples/01_hello_gbase.py

GBase is not a pip library — it's a complete agent framework.
You run it directly from the clone directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run

if __name__ == "__main__":
    run(mode="cli")

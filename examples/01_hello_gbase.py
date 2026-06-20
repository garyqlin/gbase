"""Hello GBase — your first agent in 10 lines.

Steps:
    1. Install: pip install gbase
    2. Init:    gbase init
    3. Edit .env with your API key
    4. Run:     python3 examples/01_hello_gbase.py

Or clone the repo for the full framework experience:
    git clone https://github.com/garyqlin/gbase.git
    cd gbase && python3 examples/01_hello_gbase.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run

if __name__ == "__main__":
    run(mode="cli")

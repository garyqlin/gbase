"""Run GBase as an HTTP server — talk to your agent via curl.

Usage:
    cd /path/to/gbase
    python3 examples/03_agent_as_server.py

    # In another terminal:
    curl http://localhost:8420/ask -X POST \
        -H "Content-Type: application/json" \
        -d '{"message": "Hello from curl!"}'
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "8420"
    print(f"Starting GBase server on port {port}...")
    print(
        f"Try: curl http://localhost:{port}/ask -X POST -H 'Content-Type: application/json' -d '{{\"message\": \"hi\"}}'"
    )
    run(mode="http", port=int(port))

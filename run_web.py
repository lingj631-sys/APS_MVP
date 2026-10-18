"""
Entry script to launch APS MVP Web Server.

Access the UI at:
- http://127.0.0.1:8000
- http://localhost:8000
"""
import sys

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uvicorn

if __name__ == "__main__":
    print("=" * 70)
    print("  [APS MVP] Starting Web Server...")
    print("  [URL] http://127.0.0.1:8000")
    print("  [URL] http://localhost:8000")
    print("=" * 70)
    uvicorn.run("src.server:app", host="0.0.0.0", port=8000, reload=False)

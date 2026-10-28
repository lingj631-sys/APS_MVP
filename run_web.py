"""
Entry script to launch APS MVP Web Server.

Access the UI at:
- http://127.0.0.1:8000
- http://localhost:8000
"""
import os
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
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    # In production Docker, RELOAD is set to false; defaults to true for local development
    default_reload = "false" if os.getenv("APP_ENV") == "production" else "true"
    reload = os.getenv("RELOAD", default_reload).lower() in ("true", "1", "yes")

    print("=" * 70)
    print(f"  [APS MVP] Starting Web Server on {host}:{port} (reload={reload})...")
    print(f"  [URL] http://127.0.0.1:{port}")
    print(f"  [URL] http://localhost:{port}")
    print("=" * 70)
    uvicorn.run("src.server:app", host=host, port=port, reload=reload)

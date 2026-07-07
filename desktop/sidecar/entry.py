"""PyInstaller entry point for the bundled backend sidecar.

Electron spawns the frozen binary with ``JOB_APPLIER_API_PORT`` and
``JOB_APPLIER_DATA_DIR`` set; this runs the FastAPI app in production mode (no
reload) on that port. Kept deliberately tiny so PyInstaller's analysis starts
from a clean, explicit root.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    port = int(os.environ.get("JOB_APPLIER_API_PORT", "8000"))
    host = os.environ.get("JOB_APPLIER_API_HOST", "127.0.0.1")
    uvicorn.run("job_applier.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

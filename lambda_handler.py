"""Lambda entry point for the web surface.

Mangum adapts the ASGI app to Lambda's event shape, so `web/app.py` stays a
plain FastAPI application that runs identically under `uvicorn` locally. Nothing
in `src/` or `web/` knows it is on Lambda.

The one thing that does change is where runtime state goes: the deployment
package is read-only, so `AFH_DATA_DIR` points at /tmp. That directory survives
between invocations on a warm container, which is exactly the lifetime the
extraction cache wants — a warm container skips the model calls for extraction,
a cold one pays for them once.
"""

from mangum import Mangum

from web.app import app

handler = Mangum(app, lifespan="off")

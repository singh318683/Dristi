# Vercel entrypoint for Drishti. Place this file at api/index.py in the repo.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")
sys.path.insert(0, SERVER)
os.chdir(SERVER)  # app.py uses paths like ephe/ and data/ relative to server/

# Vercel's filesystem is read-only except /tmp, so keep the SQLite DB there.
os.environ.setdefault("DRISHTI_DB", "/tmp/app.db")

from app import app  # noqa: E402,F401  (FastAPI instance Vercel looks for)

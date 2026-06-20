import os
import sys
import tempfile
from pathlib import Path

# Ensure the backend root is importable as `app.*`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolate test DB + storage before app modules read settings.
_tmp = tempfile.mkdtemp(prefix="cadmaker-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("STORAGE_DIR", f"{_tmp}/storage")
os.environ.setdefault("LLM_PROVIDER", "mock")
# Tests run as the offline mock; mark the env so gating allows it explicitly.
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_ALLOW_MOCK_DRAWING", "true")
# Trusted, deterministic mock programs run in-process (still AST-linted) for CI
# speed. Untrusted LLM code always uses the subprocess sandbox regardless.
os.environ.setdefault("CADMAKER_SANDBOX", "inprocess")

# Create tables for the isolated test DB (TestClient at module scope does not
# fire FastAPI startup events).
from app.database import init_db  # noqa: E402

init_db()

import itertools  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_email_counter = itertools.count()


def _signup(client: "TestClient") -> dict:
    """Create a fresh user and return {'headers': ..., 'user': ..., 'token': ...}."""
    email = f"user{next(_email_counter)}_{os.getpid()}@example.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {
        "token": body["access_token"],
        "user": body["user"],
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
    }


@pytest.fixture
def client() -> TestClient:
    # Imported lazily so test modules that don't need the HTTP app (and thus the
    # heavy CAD kernel) can run without importing it.
    from app.main import app

    return TestClient(app)


@pytest.fixture
def auth(client: TestClient) -> dict:
    """A signed-up user with ready-to-use Authorization headers."""
    return _signup(client)


@pytest.fixture
def auth2(client: TestClient) -> dict:
    """A second, distinct user (for isolation tests)."""
    return _signup(client)


@pytest.fixture
def legacy_engine():
    """Force the legacy template-first pipeline for tests that specifically
    exercise it (regenerate/modify/package/drawing/circle-edit/localized-edit and
    the cadquery_program compiler). The CadPlan feature-graph engine is the
    production default; these features operate on a DesignSpec the legacy path
    produces. New parts default to the feature graph (see CAD_ENGINE)."""
    from app.config import settings

    prev = settings.cad_engine
    settings.cad_engine = "legacy"
    try:
        yield
    finally:
        settings.cad_engine = prev

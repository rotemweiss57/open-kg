import os

os.environ.update(ENVIRONMENT="test", DEMO_MODE="true", EXECUTION_MODE="none", DATABASE_URL="sqlite://",
                  TAVILY_API_KEY="", OPENAI_API_KEY="", ANTHROPIC_API_KEY="", VALAR_API_KEY="", MODEL_PROVIDER="",
                  MODEL_FALLBACKS="", FIXTURE_STEP_DELAY="0")

import pytest  # noqa: E402

from openkg import db  # noqa: E402
from openkg.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Each test gets its own database. TEST_DATABASE_URL=postgresql://... runs the suite on Postgres."""
    get_settings.cache_clear()
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = db.make_engine(url)
        db.Base.metadata.drop_all(engine)
    else:
        engine = db.make_engine(f"sqlite:///{tmp_path}/test.db")
    db.set_engine(engine)
    db.Base.metadata.create_all(engine)
    from openkg.api import routes
    routes._login_limiter.hits.clear()
    routes._run_limiter.hits.clear()
    yield
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def workspace():
    from openkg.db import session_scope
    from openkg.security import create_user
    with session_scope() as s:
        user, ws = create_user(s, "owner@example.com", "correct horse battery", name="Owner")
        return {"user_id": user.id, "workspace_id": ws.id}


@pytest.fixture
def make_graph(workspace):
    from openkg.agent.fixture import get_scenario
    from openkg.db import session_scope
    from openkg.models import Graph
    from openkg.seed import graph_from_scenario

    def _make(scenario: str | None = "series-b-product-leaders", **kw) -> str:
        with session_scope() as s:
            if scenario:
                g = graph_from_scenario(s, workspace["workspace_id"], get_scenario(scenario))
            else:
                g = Graph(workspace_id=workspace["workspace_id"], name="Live graph", **kw)
                s.add(g)
                s.flush()
            for k, v in kw.items():
                setattr(g, k, v)
            return g.id
    return _make

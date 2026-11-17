"""`openkg setup` and `openkg doctor`: no network, no real keys, nothing printed that is secret."""
import stat

import httpx

from openkg import setup as setup_mod
from openkg.setup import FAIL, OK, WARN, check_key, read_env, write_env

GOOD = {"tvly-good", "valar-good", "sk-ant-good", "sk-good"}


def transport(calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ") or request.headers.get("x-api-key", "")
        if calls is not None:
            calls.append((request.method, str(request.url)))
        return httpx.Response(200 if key in GOOD else 401, json={"data": []})
    return httpx.MockTransport(handler)


def run(tmp_path, monkeypatch, *, secrets=(), answers=(), environ=None, **kw):
    monkeypatch.setattr(setup_mod, "repo_root", lambda: tmp_path)
    (tmp_path / ".env.example").write_text("# OpenKG\nTAVILY_API_KEY=\nVALAR_API_KEY=\nMODEL_PROVIDER=\nDEFAULT_RUN_BUDGET_USD=1.0\n")
    secrets, answers, out = iter(secrets), iter(answers), []
    code = setup_mod.setup(ask=lambda _: next(answers, ""), ask_secret=lambda _: next(secrets, ""), out=out.append,
                           environ=environ or {}, transport=transport(), **kw)
    return code, "\n".join(out), read_env(tmp_path / ".env")


def test_guided_setup_writes_keys_privately_and_never_prints_them(tmp_path, monkeypatch):
    code, text, env = run(tmp_path, monkeypatch, secrets=["tvly-good", "valar-good"], answers=["1", "2.5"])
    assert code == 0 and "Live research: ready" in text
    assert env["TAVILY_API_KEY"] == "tvly-good" and env["VALAR_API_KEY"] == "valar-good"
    assert env["MODEL_PROVIDER"] == "valar" and env["DEFAULT_RUN_BUDGET_USD"] == "2.5"
    assert "tvly-good" not in text and "valar-good" not in text
    path = tmp_path / ".env"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().startswith("# OpenKG")  # template comments are kept


def test_a_rejected_key_is_not_saved_and_can_be_retried(tmp_path, monkeypatch):
    code, text, env = run(tmp_path, monkeypatch, secrets=["tvly-typo", "tvly-good", "sk-ant-good"], answers=["2", ""])
    assert "rejected this key" in text and env["TAVILY_API_KEY"] == "tvly-good"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-good" and env["MODEL_PROVIDER"] == "anthropic" and code == 0
    assert "tvly-typo" not in text


def test_skipping_everything_leaves_a_working_demo_config(tmp_path, monkeypatch):
    code, text, env = run(tmp_path, monkeypatch, answers=["s", ""])
    assert code == 0 and "not configured" in text and env.get("TAVILY_API_KEY", "") == ""


def test_rerun_keeps_saved_keys_when_the_user_presses_enter(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, secrets=["tvly-good", "valar-good"], answers=["1", ""])
    code, text, env = run(tmp_path, monkeypatch, secrets=["", ""], answers=["", ""])
    assert code == 0 and "keeping the saved key" in text and env["TAVILY_API_KEY"] == "tvly-good"
    assert env["VALAR_API_KEY"] == "valar-good" and "Live research: ready" in text


def test_non_interactive_setup_reads_the_environment(tmp_path, monkeypatch):
    code, text, env = run(tmp_path, monkeypatch, assume_yes=True,
                          environ={"TAVILY_API_KEY": "tvly-good", "OPENAI_API_KEY": "sk-good"})
    assert code == 0 and env["MODEL_PROVIDER"] == "openai" and env["OPENAI_API_KEY"] == "sk-good"
    code, text, env = run(tmp_path, monkeypatch, assume_yes=True, environ={"TAVILY_API_KEY": "tvly-bad"})
    assert "rejected" in text and env["TAVILY_API_KEY"] == "tvly-good"  # a bad key never replaces a good one


def test_key_checks_distinguish_rejected_from_unreachable():
    assert check_key("tavily", "tvly-good", transport=transport())[0] == OK
    assert check_key("anthropic", "nope", transport=transport())[0] == FAIL
    assert check_key("openai", "has space", transport=transport())[0] == FAIL

    def down(request):
        raise httpx.ConnectError("offline")
    assert check_key("valar", "valar-good", transport=httpx.MockTransport(down))[0] == WARN
    calls = []
    check_key("tavily", "tvly-good", transport=transport(calls))
    assert calls == [("GET", "https://api.tavily.com/usage")]  # a free, read-only endpoint


def test_env_file_round_trip_preserves_other_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# keep me\nFOO="bar baz"\nexport TAVILY_API_KEY=old # comment\n\nOTHER=1\n')
    write_env(path, {"TAVILY_API_KEY": "new", "ADDED": "x"})
    assert read_env(path) == {"FOO": "bar baz", "TAVILY_API_KEY": "new", "OTHER": "1", "ADDED": "x"}
    assert path.read_text().splitlines()[0] == "# keep me"


def test_doctor_reports_without_secrets(monkeypatch):
    from openkg.config import get_settings
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret")
    get_settings.cache_clear()
    out = []
    code = setup_mod.doctor(out=out.append)
    text = "\n".join(out)
    assert code == 0 and "Tavily key" in text and "tvly-super-secret" not in text
    assert "Database" in text and "No blocking problems." in text

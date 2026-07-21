"""Remote search (D48): querying the configured remotes' ``/search`` endpoints,
merging hits in priority order, per-remote resilience, and the CLI's rendered
output — all against a fake index (httpx.MockTransport)."""

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.search as search
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.search import SearchError, search_remotes

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    return home_dir


def summary(namespace, name, version="1.0.0", description=""):
    return {
        "namespace": namespace,
        "name": name,
        "latest_version": version,
        "description": description,
    }


def fake_index(monkeypatch, per_remote):
    """Route ``GET /search`` by request host to ``per_remote`` (host -> handler
    taking the httpx.Request, returning an httpx.Response). Returns the recorded
    request list."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/search"
        return per_remote[request.url.host](request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(search, "_client", lambda: httpx.Client(transport=transport))
    return requests


def ok(results):
    return lambda request: httpx.Response(200, json={"results": results})


# --- search_remotes: selection and merging ---------------------------------


def test_no_remotes_configured_is_an_error(monkeypatch):
    with pytest.raises(SearchError, match="no remotes configured"):
        search_remotes([], None, "x", None, None)


def test_unknown_forced_remote_is_an_error(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    with pytest.raises(SearchError, match="no remote named 'b'"):
        search_remotes(remotes, "b", "x", None, None)


def test_hits_merge_across_remotes_in_priority_order(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    fake_index(
        monkeypatch,
        {
            "a.example": ok([summary("acme", "one")]),
            "b.example": ok([summary("infra", "two")]),
        },
    )
    outcome = search_remotes(remotes, None, "", None, None)
    assert [(h.remote, h.package.name) for h in outcome.hits] == [
        ("a", "one"),
        ("b", "two"),
    ]
    assert outcome.warnings == []


def test_forced_remote_queries_only_that_one(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    requests = fake_index(
        monkeypatch,
        {
            "a.example": ok([summary("acme", "one")]),
            "b.example": ok([summary("infra", "two")]),
        },
    )
    outcome = search_remotes(remotes, "b", "", None, None)
    assert {r.url.host for r in requests} == {"b.example"}
    assert [h.package.name for h in outcome.hits] == ["two"]


def test_query_and_filters_are_passed_through(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    requests = fake_index(monkeypatch, {"a.example": ok([])})
    search_remotes(remotes, None, "tool", "windows", "python")
    params = requests[0].url.params
    assert params["q"] == "tool"
    assert params["platform"] == "windows"
    assert params["language"] == "python"


def test_absent_filters_are_omitted_from_the_query(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    requests = fake_index(monkeypatch, {"a.example": ok([])})
    search_remotes(remotes, None, "tool", None, None)
    params = requests[0].url.params
    assert "platform" not in params
    assert "language" not in params


# --- resilience ------------------------------------------------------------


def test_one_failing_remote_becomes_a_warning_not_a_failure(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    fake_index(
        monkeypatch,
        {
            "a.example": lambda request: httpx.Response(503, text="down"),
            "b.example": ok([summary("infra", "two")]),
        },
    )
    outcome = search_remotes(remotes, None, "", None, None)
    assert [h.package.name for h in outcome.hits] == ["two"]
    assert len(outcome.warnings) == 1
    assert "a" in outcome.warnings[0]


def test_all_remotes_failing_is_a_hard_error(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    fake_index(
        monkeypatch,
        {
            "a.example": lambda request: httpx.Response(500, text="boom"),
            "b.example": lambda request: httpx.Response(500, text="boom"),
        },
    )
    with pytest.raises(SearchError):
        search_remotes(remotes, None, "", None, None)


# --- CLI -------------------------------------------------------------------


def test_cli_renders_hits_in_a_table(home, monkeypatch):
    save_remotes(home, [Remote("a", "https://a.example")])
    fake_index(
        monkeypatch,
        {"a.example": ok([summary("acme", "widget", "2.1.0", "A widget")])},
    )
    result = runner.invoke(app, ["search", "widget"])
    assert result.exit_code == 0
    assert "acme/widget" in result.stdout
    assert "2.1.0" in result.stdout
    assert "A widget" in result.stdout


def test_cli_shows_remote_column_only_when_hits_span_remotes(home, monkeypatch):
    save_remotes(home, [Remote("a", "https://a.example"), Remote("b", "https://b.example")])
    fake_index(
        monkeypatch,
        {
            "a.example": ok([summary("acme", "one")]),
            "b.example": ok([summary("infra", "two")]),
        },
    )
    result = runner.invoke(app, ["search"])
    assert result.exit_code == 0
    assert "Remote" in result.stdout


def test_cli_no_matches_reports_cleanly(home, monkeypatch):
    save_remotes(home, [Remote("a", "https://a.example")])
    fake_index(monkeypatch, {"a.example": ok([])})
    result = runner.invoke(app, ["search", "nope"])
    assert result.exit_code == 0
    assert "No packages found." in result.stdout


def test_cli_no_remotes_configured_errors(home, monkeypatch):
    result = runner.invoke(app, ["search", "x"])
    assert result.exit_code == 1
    assert "no remotes configured" in result.stdout


def test_cli_reports_a_down_remote_as_a_warning(home, monkeypatch):
    save_remotes(home, [Remote("a", "https://a.example"), Remote("b", "https://b.example")])
    fake_index(
        monkeypatch,
        {
            "a.example": lambda request: httpx.Response(503, text="down"),
            "b.example": ok([summary("infra", "two")]),
        },
    )
    result = runner.invoke(app, ["search"])
    assert result.exit_code == 0
    assert "warning" in result.stdout
    assert "infra/two" in result.stdout

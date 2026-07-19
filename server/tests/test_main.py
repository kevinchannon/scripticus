import pytest

from scripticus_server import __version__
from scripticus_server import main as main_module
from scripticus_server.main import _banner, _parse_args, main


def test_defaults_bind_localhost_port_8000():
    args = _parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_host_and_port_are_configurable():
    args = _parse_args(["--host", "0.0.0.0", "--port", "9999"])
    assert args.host == "0.0.0.0"
    assert args.port == 9999


def test_banner_reports_version_host_and_port():
    banner = _banner("127.0.0.1", 8000)
    assert f"scripticus-svr {__version__}" in banner
    assert "http://127.0.0.1:8000" in banner


def test_main_prints_banner_and_serves_on_requested_interface(monkeypatch, capsys):
    served_with = {}

    def fake_run(app, host, port):
        served_with.update(host=host, port=port)

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    main(["--host", "0.0.0.0", "--port", "9999"])

    assert served_with == {"host": "0.0.0.0", "port": 9999}
    assert f"scripticus-svr {__version__}" in capsys.readouterr().out


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as excinfo:
        _parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "scripticus-svr" in capsys.readouterr().out

"""The `pysdrlib` command (pysdrlib/cli.py) sends each subcommand to the module that does it"""
import builtins
import sys

import pytest

from pysdrlib import cli
from pysdrlib.remote import Server


def test_the_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["-h"])
    out = capsys.readouterr().out
    assert exit.value.code == 0 and all(name in out for name in ("build", "server", "relay", "client"))


def test_build_runs_without_pyzmq(monkeypatch, capsys):
    real = builtins.__import__
    def no_zmq(name, *a, **k):
        if name == "zmq":
            raise ImportError("no zmq")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_zmq)
    with pytest.raises(SystemExit) as exit:
        cli.main(["build"])
    assert exit.value.code == 0 and "hackrf" in capsys.readouterr().out


def test_the_remote_commands_say_what_to_install_without_pyzmq(monkeypatch):
    real = builtins.__import__
    def no_zmq(name, *a, **k):
        if name == "zmq":
            raise ImportError("no zmq")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_zmq)
    with pytest.raises(SystemExit, match=r"pip install pysdrlib\[remote\]"):
        cli.main(["client", "tcp://127.0.0.1:1", "list"])


@pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")
def test_the_remote_commands_are_reached_through_it(tmp_path, capsys):
    with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0) as server:
        server.open_device("sim")
        cli.main(["client", server.endpoint, "list"])
    out = capsys.readouterr().out
    assert "types: sim" in out and "sim-0" in out


def test_an_unknown_command_is_refused(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["frobnicate"])
    assert exit.value.code == 2


@pytest.mark.skipif(sys.platform == "win32", reason="ZeroMQ has no ipc:// transport on Windows")
def test_the_remote_commands_can_still_be_run_on_their_own(tmp_path, capsys):
    from pysdrlib.remote import cli as remote_cli
    with Server(f"ipc://{tmp_path}/c2", devices=["sim"], lease=0) as server:
        remote_cli.main(["client", server.endpoint, "list"])
    assert "types: sim" in capsys.readouterr().out

import subprocess
import sys


def test_cli_help():
    res = subprocess.run([sys.executable, "-m", "app.cli", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "zed-proxy" in res.stdout
    assert "start" in res.stdout
    assert "sync" in res.stdout


def test_cli_completions():
    for sh in ["bash", "zsh", "fish"]:
        res = subprocess.run([sys.executable, "-m", "app.cli", "completion", sh], capture_output=True, text=True)
        assert res.returncode == 0
        assert "zed-proxy" in res.stdout


def test_cli_status_offline():
    res = subprocess.run([sys.executable, "-m", "app.cli", "status", "--port", "59999"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "offline" in res.stdout or "unreachable" in res.stdout

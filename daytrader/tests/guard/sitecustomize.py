"""Loaded into every test: no real network, desktop alerts or sounds."""
import socket
import subprocess

_connect, _run, _popen = socket.socket.connect, subprocess.run, subprocess.Popen


def connect(self, address):
    if self.family == socket.AF_INET and address[0] not in ("127.0.0.1", "localhost"):
        raise OSError(f"test tried to reach {address}")
    return _connect(self, address)


def run(cmd, *a, **k):
    if cmd and cmd[0] in ("osascript", "afplay"):
        raise AssertionError(f"test tried to run {cmd[0]}")
    return _run(cmd, *a, **k)


socket.socket.connect, subprocess.run = connect, run

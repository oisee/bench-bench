"""Skip integration tests only when their required OS facilities are unavailable."""
import shutil
import socket
import subprocess

import pytest


def pytest_collection_modifyitems(items):
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
        tcp = True
    except OSError:
        tcp = False
    bwrap = shutil.which('bwrap') and subprocess.run(
        ['bwrap', '--ro-bind', '/', '/', '--unshare-pid', '--unshare-ipc', '--unshare-net', 'true'],
        capture_output=True).returncode == 0
    for item in items:
        cls = item.cls.__name__ if item.cls else ''
        if not bwrap and cls in ('GradeHiddenDir', 'SandboxNoWeb', 'Egress', 'TestSandboxKey'):
            item.add_marker(pytest.mark.skip(reason='kernel does not support bwrap namespaces'))
        if not tcp and cls in ('TestProxyKey', 'TestProxySnapshots', 'Egress'):
            item.add_marker(pytest.mark.skip(reason='local TCP sockets unavailable'))

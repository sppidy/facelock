"""Root socket-activated verification for unprivileged PAM callers.

There is no requested username, executable, configuration or enrollment write.
The kernel's peer UID selects the sole account that a connection can verify.
"""
import argparse
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess

from . import security
from .feedback import peer_uid

SOCKET = Path('/run/facelock-auth/auth.sock')
REQUEST = b'AUTH\n'
SUCCESS = b'OK\n'
FAILURE = b'NO\n'


def receive(conn, limit):
    result = b''
    while len(result) < limit and not result.endswith(b'\n'):
        chunk = conn.recv(limit - len(result))
        if not chunk:
            break
        result += chunk
    return result


def verify_uid(uid, run=subprocess.run):
    # Root PAM callers use the ordinary helper with PAM's target account.
    if uid == 0:
        return False
    user = pwd.getpwuid(uid).pw_name
    library = Path(__file__).resolve().parents[1]
    security.trusted_path(library)
    command = ['/usr/bin/python3', '-I', str(library / 'runner.py'),
               str(library / 'verify.py'), '--user', user, '--quiet']
    # Runner validates config/runtime, serializes capture and enforces the
    # configured <=40s authentication deadline. systemd bounds the whole tree.
    result = run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL,
                 env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=43)
    return result.returncode == 0


def serve_connection(conn, verify=verify_uid):
    conn.settimeout(1)
    uid = peer_uid(conn)
    try:
        if receive(conn, len(REQUEST) + 1) != REQUEST:
            conn.sendall(FAILURE)
            return
        accepted = verify(uid)
        conn.sendall(SUCCESS if accepted else FAILURE)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        try:
            conn.sendall(FAILURE)
        except OSError:
            pass


def check(user, path=SOCKET):
    # Prevent a PAM target different from the peer account from inheriting
    # this peer's successful biometric check.
    if pwd.getpwnam(user).pw_uid != os.getuid():
        return False
    security.trusted_path(path.parent)
    info = path.lstat()
    if info.st_uid != 0 or not stat.S_ISSOCK(info.st_mode):
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(45)
        conn.connect(str(path))
        if peer_uid(conn) != 0:
            return False
        conn.sendall(REQUEST)
        return receive(conn, len(SUCCESS) + 1) == SUCCESS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.serve:
            if os.geteuid() != 0:
                return 1
            # systemd Accept=yes supplies exactly one accepted connection.
            with socket.socket(fileno=os.dup(0)) as conn:
                if conn.family != socket.AF_UNIX or conn.type != socket.SOCK_STREAM:
                    return 1
                serve_connection(conn)
            return 0
        if os.environ.get('PAM_TYPE') != 'auth':
            return 1
        return 0 if check(os.environ.get('PAM_USER', '')) else 1
    except (OSError, ValueError, KeyError):
        return 1

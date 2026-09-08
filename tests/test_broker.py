import os
from pathlib import Path
import socket
import stat
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from facelock import broker


class BrokerTests(unittest.TestCase):
    def exchange(self, request, verdict=True):
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        verify = Mock(return_value=verdict)
        errors = []
        def serve():
            try:
                with patch('facelock.broker.peer_uid', return_value=1000):
                    broker.serve_connection(server, verify)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve)
        thread.start()
        client.settimeout(2)
        client.sendall(request)
        reply = client.recv(20)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        return reply, verify

    def test_authentication_uses_kernel_peer_and_explicit_success(self):
        for accepted in (True, False):
            reply, verify = self.exchange(broker.REQUEST, accepted)
            self.assertEqual(reply, broker.SUCCESS if accepted else broker.FAILURE)
            verify.assert_called_once_with(1000)

    def test_requested_accounts_commands_and_oversized_input_are_rejected(self):
        for request in (b'AUTH root\n', b'EXEC\n', b'x' * 20, b'OK\n'):
            reply, verify = self.exchange(request)
            self.assertEqual(reply, broker.FAILURE)
            verify.assert_not_called()

    def test_only_fixed_verifier_and_uid_account_reach_subprocess(self):
        run = Mock(return_value=SimpleNamespace(returncode=0))
        with patch('facelock.broker.pwd.getpwuid', return_value=SimpleNamespace(pw_name='alice')), \
             patch('facelock.broker.security.trusted_path'):
            self.assertTrue(broker.verify_uid(1000, run))
        args, kw = run.call_args
        self.assertEqual(args[0][-3:], ['--user', 'alice', '--quiet'])
        self.assertEqual(kw['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertFalse(broker.verify_uid(0, run))
        self.assertEqual(run.call_count, 1)
        run.return_value.returncode = 1
        with patch('facelock.broker.pwd.getpwuid', return_value=SimpleNamespace(pw_name='alice')), \
             patch('facelock.broker.security.trusted_path'):
            self.assertFalse(broker.verify_uid(1000, run))

    def test_target_account_mismatch_never_connects(self):
        with patch('facelock.broker.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=2000)), \
             patch('facelock.broker.os.getuid', return_value=1000), \
             patch('facelock.broker.socket.socket') as connect:
            self.assertFalse(broker.check('bob'))
            connect.assert_not_called()

    def test_client_requires_root_peer_and_exact_success(self):
        path = Mock()
        path.lstat.return_value = SimpleNamespace(st_uid=0, st_mode=stat.S_IFSOCK | 0o666)
        for uid, reply, accepted in ((1000, b'OK\n', False), (0, b'OK\n', True),
                                      (0, b'NO\n', False), (0, b'OKAY', False), (0, b'', False)):
            with patch('facelock.broker.pwd.getpwnam', return_value=SimpleNamespace(pw_uid=1000)), \
                 patch('facelock.broker.os.getuid', return_value=1000), \
                 patch('facelock.broker.security.trusted_path'), \
                 patch('facelock.broker.peer_uid', return_value=uid), \
                 patch('facelock.broker.receive', return_value=reply), \
                 patch('facelock.broker.socket.socket'):
                self.assertEqual(broker.check('alice', path), accepted)

    def test_unavailable_broker_and_non_auth_pam_calls_fail(self):
        with patch.dict(os.environ, {'PAM_TYPE': 'account'}, clear=True):
            self.assertEqual(broker.main(['--check']), 1)
        with patch.dict(os.environ, {'PAM_TYPE': 'auth', 'PAM_USER': 'alice'}, clear=True), \
             patch('facelock.broker.check', side_effect=FileNotFoundError):
            self.assertEqual(broker.main(['--check']), 1)

    def test_timeout_or_verifier_error_never_reports_success(self):
        conn = Mock()
        conn.recv.return_value = broker.REQUEST
        for error in (OSError('camera unavailable'), subprocess.TimeoutExpired('verify', 43)):
            with patch('facelock.broker.peer_uid', return_value=1000):
                broker.serve_connection(conn, Mock(side_effect=error))
            conn.sendall.assert_called_with(broker.FAILURE)


class StatusLabelTests(unittest.TestCase):
    def test_status_updates_only_label_and_queued_updates_stop_after_destroy(self):
        from facelock.lock_ui import StatusLabel
        label, glib = Mock(), Mock()
        with patch('facelock.lock_ui.threading.Thread'):
            status = StatusLabel(label, glib)
        status._show('Face matched')
        label.set_text.assert_called_once_with('Face matched')
        destroy = label.connect.call_args.args[1]
        destroy(label)
        status._show('Looking for your face')
        self.assertEqual(label.set_text.call_count, 1)
        self.assertTrue(status.stopped.is_set())

    def test_missing_feedback_service_clears_label_without_affecting_pam(self):
        from facelock.lock_ui import StatusLabel
        label, glib = Mock(), Mock()
        with patch('facelock.lock_ui.threading.Thread'):
            status = StatusLabel(label, glib)
        with patch('facelock.lock_ui.feedback.endpoint', side_effect=FileNotFoundError), \
             patch.object(status.stopped, 'wait', side_effect=lambda _: status.stopped.set()):
            status._poll()
        glib.idle_add.assert_called_once_with(status._show, '')

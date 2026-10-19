#!/usr/bin/env python3
"""Local fake Telegram API tests. No Telegram credentials/network required."""
from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import install_progress_cleanup as installer

ROOT = Path(__file__).parent
def sample_relay(parallel: bool = False) -> str:
    # Synthetic *minimal* fixtures: no missing files or external imports.
    return ("from __future__ import annotations\n"
            "from typing import Any\n"
            "PROGRESS_INTERVAL = 7\n"
            "def _progress_notifier(job: dict[str, Any]) -> None:\n"
            "    _bot_api(\"sendMessage\", {\"text\": _progress_text(job)})\n"
            "    _bot_api(\"editMessageText\", {\"text\": _progress_text(job)})\n"
            "    x = PROGRESS_INTERVAL\n"
            "    return\n\n"
            "def _telegram_setup_error():\n"
            "    return None\n\n"
            "def _telegram_send(job, file_path):\n"
            "    pass\n\n"
            + ("# parallel-enabled relay\n" if parallel else "# sequential relay\n"))


class Clock:
    def __init__(self, job, advance_to_sent='sent'):
        self.seconds = 0
        self.sleeps = []
        self.job = job
        self.on_first_sleep_status = advance_to_sent
        self.changed = False

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds
        self.sleeps.append(seconds)
        if not self.changed and self.on_first_sleep_status:
            self.changed = True
            self.job['status'] = self.on_first_sleep_status


def load_notifier():
    source = installer.NEW_NOTIFIER
    parsed = ast.parse(source)
    return compile(parsed, '<notifier>', 'exec')


class CleanupTests(unittest.TestCase):
    def execute_job(self, *, start='uploading', terminal='sent', cleanup='1', failure_cleanup='1', fail_first_delete=False):
        job = {'status': start, 'title': 'Lecture 1', 'error': 'network problem'}
        clock = Clock(job, advance_to_sent=terminal)
        calls = []
        delete_attempts = 0

        def bot_api(method, params):
            nonlocal delete_attempts
            calls.append((method, dict(params)))
            if method == 'sendMessage':
                return {'message_id': 5544}
            if method == 'deleteMessage':
                delete_attempts += 1
                if fail_first_delete and delete_attempts == 1:
                    raise TimeoutError('simulation: Bot API down')
                return True
            if method == 'editMessageText':
                return True
            raise AssertionError(f'Unexpected Bot API operation: {method}')

        d = {
            'Any': Any,
            'os': types.SimpleNamespace(getenv=lambda k, default='': {
                'TELEGRAM_CHAT_ID': '-1001234567',
                'TELEGRAM_PROGRESS_CLEANUP': cleanup,
                'TELEGRAM_PROGRESS_CLEANUP_FAILURES': failure_cleanup,
            }.get(k, default)),
            'time': clock,
            'PROGRESS_INTERVAL': 7,
            'log': logging.getLogger('test_cleanup'),
            '_bot_api': bot_api,
            '_progress_text': lambda j: f"{j['status']} message",
        }
        exec(load_notifier(), d)
        d['_progress_notifier'](job)
        return calls, clock

    def test_success_deletes_only_own_progress_message(self):
        calls, clock = self.execute_job()
        self.assertEqual([m for m, _ in calls], ['sendMessage', 'deleteMessage'])
        self.assertEqual(calls[-1][1], {'chat_id': '-1001234567', 'message_id': 5544})
        self.assertIn(3, clock.sleeps)

    def test_error_shows_failure_then_removes_status_text(self):
        calls, clock = self.execute_job(terminal='failed')
        self.assertEqual([m for m, _ in calls], ['sendMessage', 'editMessageText', 'deleteMessage'])
        self.assertEqual(calls[1][1]['text'], 'failed message')
        self.assertIn(30, clock.sleeps)

    def test_delete_disabled_keeps_final_status(self):
        calls, _ = self.execute_job(cleanup='0')
        self.assertEqual([m for m, _ in calls], ['sendMessage', 'editMessageText'])

    def test_failed_cleanup_option_keeps_error_for_diagnosis(self):
        calls, _ = self.execute_job(terminal='failed', failure_cleanup='0')
        self.assertEqual([m for m, _ in calls], ['sendMessage', 'editMessageText'])

    def test_retry_transient_delete_error(self):
        calls, clock = self.execute_job(fail_first_delete=True)
        self.assertEqual([m for m, _ in calls], ['sendMessage','deleteMessage','deleteMessage'])
        self.assertIn(2, clock.sleeps)
        self.assertEqual(calls[2][1]['message_id'], 5544)

    def test_job_completed_before_notifier_starts_creates_no_message(self):
        calls, _ = self.execute_job(start='sent', terminal=None)
        self.assertEqual(calls, [])

    def test_notifier_only_calls_three_safe_bot_api_methods(self):
        source = installer.NEW_NOTIFIER
        for forbidden in ['deleteMessages', 'deleteChatHistory', 'sendVideo', 'sendDocument']:
            self.assertNotIn(f'_bot_api("{forbidden}"', source)

    def test_installer_patches_both_relay_formats_idempotently(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                original = sample_relay(parallel)
                changed = installer.patch_text(original)
                self.assertIn(installer.MARKER, changed)
                self.assertEqual(installer.patch_text(changed), changed)
                self.assertEqual(changed.count('def _telegram_send('), original.count('def _telegram_send('))
                self.assertEqual(changed.count('@app.post('), original.count('@app.post('))

    def test_current_relay_if_present(self):
        path = Path('relay_api.py')
        if not path.exists():
            self.skipTest('relay_api.py not in current folder')
        source = path.read_text(encoding='utf-8')
        patched = installer.patch_text(source)
        ast.parse(patched)

    def test_pipeline_marker_does_not_interfere(self):
        original = sample_relay(parallel=True)
        with_marker = original + "\n# PIPELINE_UPLOAD_NEXT_V1\n"
        changed = installer.patch_text(with_marker)
        self.assertIn('# PIPELINE_UPLOAD_NEXT_V1',changed)
        self.assertIn(installer.MARKER,changed)
        ast.parse(changed)


if __name__ == '__main__':
    unittest.main(verbosity=2)

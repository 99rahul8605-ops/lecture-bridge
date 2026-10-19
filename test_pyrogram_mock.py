"""Offline tests. No Telegram authentication/network needed."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("relay_sender_pyrogram", ROOT / "relay_sender_pyrogram.py")
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)


class FakePeerIdInvalid(Exception):
    pass


class FakeClient:
    last = None
    should_fail = False
    missing_peer = False

    def __init__(self, name, **kwargs):
        self.name, self.config = name, kwargs
        self.calls = []
        self.exited = False
        FakeClient.last = self
        self.storage = types.SimpleNamespace(update_peers=self.update_peers)
        self.seeded = None

    async def resolve_peer(self, chat_id):
        if self.missing_peer:
            raise FakePeerIdInvalid()
        return chat_id

    async def update_peers(self, peers):
        self.seeded = peers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.exited = True

    async def send_video(self, **kwargs):
        self.calls.append(kwargs)
        await kwargs["progress"](1500, 3000)
        if self.should_fail:
            raise RuntimeError("fake Telegram failure")
        await kwargs["progress"](3000, 3000)
        return types.SimpleNamespace(id=5)


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.mp4 = self.folder / "Lecture 01.mp4"
        self.mp4.write_bytes(b"video fake" * 400)
        self.thumb = self.folder / "thumb.jpg"
        self.thumb.write_bytes(b"\xff\xd8" * 1234)
        self.job = {"title": "Lecture <01>", "folder": self.folder, "status": "telegram_sending"}
        self.fakepyrogram = types.ModuleType("pyrogram")
        self.fakepyrogram.Client = FakeClient
        enums = types.ModuleType("pyrogram.enums")
        enums.ParseMode = types.SimpleNamespace(HTML="html")
        errors = types.ModuleType("pyrogram.errors")
        errors.PeerIdInvalid = FakePeerIdInvalid
        self.fake_relay = types.ModuleType("relay_api")
        self.fake_relay._probe_video = lambda p: {"duration": 140, "width": 854, "height": 480}
        self.fake_relay._extract_thumbnail = lambda path, folder, duration: self.thumb
        self.fake_relay._telegram_caption = lambda title, meta, size: "escaped caption"
        self.context = patch.dict(sys.modules, {"pyrogram": self.fakepyrogram,
                                                "pyrogram.enums": enums, "pyrogram.errors": errors,
                                                "relay_api": self.fake_relay})
        self.context.start()
        self.env = patch.dict(os.environ, {"BOT_TOKEN": "test:token", "TELEGRAM_API_ID": "1234",
                    "TELEGRAM_API_HASH": "not-real", "TELEGRAM_CHAT_ID": "-100900",
                    "RELAY_DATA_DIR": self.tmp.name, "PYROGRAM_MAX_TRANSMISSIONS": "2"})
        self.env.start()
        FakeClient.should_fail = False
        FakeClient.missing_peer = False

    def tearDown(self):
        self.env.stop()
        self.context.stop()
        self.tmp.cleanup()

    def test_success_and_metadata(self):
        asyncio.run(sender.send_telegram_mp4(self.job, self.mp4))
        client = FakeClient.last
        self.assertTrue(client.exited)
        self.assertEqual(client.config["max_concurrent_transmissions"], 2)
        self.assertEqual(client.config["workers"], 1)
        kw = client.calls[0]
        self.assertEqual(kw["chat_id"], -100900)
        self.assertEqual(kw["file_name"], "Lecture 01.mp4")
        self.assertEqual(kw["parse_mode"], "html")
        self.assertEqual(kw["height"], 480)
        self.assertTrue(kw["supports_streaming"])
        self.assertEqual(self.job["telegram_uploaded"], self.mp4.stat().st_size)
        self.assertFalse(self.thumb.exists())
        self.assertTrue(self.mp4.exists())  # FFmpeg pipeline retains until sending succeeds

    def test_first_bot_session_group_peer_bootstrap(self):
        FakeClient.missing_peer = True
        asyncio.run(sender.send_telegram_mp4(self.job, self.mp4))
        self.assertEqual(FakeClient.last.seeded, [(-100900, 0, "supergroup", None, None)])

    def test_failure_cleanup_and_progress(self):
        FakeClient.should_fail = True
        with self.assertRaisesRegex(RuntimeError, "fake Telegram failure"):
            asyncio.run(sender.send_telegram_mp4(self.job, self.mp4))
        self.assertTrue(FakeClient.last.exited)
        self.assertEqual(self.job["telegram_uploaded"], 1500)
        self.assertFalse(self.thumb.exists())
        self.assertTrue(self.mp4.exists())

    def test_missing_file_no_client(self):
        self.mp4.unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or empty"):
            asyncio.run(sender.send_telegram_mp4(self.job, self.mp4))

    def test_bad_limit(self):
        with patch.dict(os.environ, {"PYROGRAM_MAX_TRANSMISSIONS": "n/a"}):
            with self.assertRaisesRegex(RuntimeError, "integer"):
                sender._upload_transmissions()

    def test_clamp_limit(self):
        with patch.dict(os.environ, {"PYROGRAM_MAX_TRANSMISSIONS": "400"}):
            self.assertEqual(sender._upload_transmissions(), 4)


class InstallerTests(unittest.TestCase):
    def test_idempotent_and_keep_old_sender(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            source = Path("/mnt/data/Parallel_Lanes_VPS_Test/relay_api.py")
            if not source.is_file():
                self.skipTest("relay example unavailable")
            shutil.copy2(source, dest / "relay_api.py")
            (dest / "requirements.txt").write_text("Flask>=3\nTelethon>=1.35\n")
            command = [sys.executable, str(ROOT / "install_pyrogram_sender.py"), str(dest)]
            for _ in range(2):
                p = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
            relay = (dest / "relay_api.py").read_text()
            self.assertEqual(relay.count("# PYROGRAM_SENDER_SWITCH_V1"), 1)
            self.assertIn("from telethon import TelegramClient", relay)
            self.assertIn("send_telegram_mp4(job, file_path)", relay)
            self.assertEqual((dest / "requirements.txt").read_text().count("Pyrogram=="), 1)
            self.assertEqual((dest / "requirements.txt").read_text().count("TgCrypto"), 1)
            self.assertTrue((dest / "relay_api.py.before-pyrogram").exists())
            self.assertTrue((dest / "relay_sender_pyrogram.py").exists())
            import ast
            ast.parse(relay)


if __name__ == "__main__":
    unittest.main()

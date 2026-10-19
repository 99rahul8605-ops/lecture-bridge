"""Offline source/syntax tests. Do not require credentials or remote network."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parent

class RestoreTests(unittest.TestCase):
    def test_flask_app_is_manual_only(self):
        src=(ROOT/'app.py').read_text()
        tree=ast.parse(src)
        self.assertIn('install_relay_routes(app)',src)
        self.assertNotIn('install_catalog_routes',src)
        self.assertNotIn('auto_catalog',src)
        self.assertNotIn('catalog_web',src)
        self.assertIn('health',src)
        self.assertTrue(any(isinstance(node,ast.FunctionDef) for node in tree.body))

    def test_userscript_is_manual_one_click(self):
        src=(ROOT/'Railway_Auto_Telegram.user.js').read_text()
        self.assertIn('@match        https://vidcloud.eu.org/play.php*',src)
        self.assertIn('Send MP4 to Telegram',src)
        self.assertIn("button.addEventListener('click'",src)
        self.assertIn('await api(`/api/relay/${encodeURIComponent(job.job_id)}/segment/${i}`',src)
        self.assertIn('const PREFETCH = 3',src)
        self.assertNotIn('queue worker',src)
        self.assertNotIn('CATALOG_SYNC_KEY',src)

    def test_rollback_script_is_nondestructive(self):
        src=(ROOT/'restore_manual.sh').read_text()
        self.assertNotIn('rm -rf',src)
        self.assertNotIn('fuser -k',src)
        self.assertIn('signal.SIGHUP',src)
        self.assertIn('catalog-control',src)

if __name__=='__main__': unittest.main(verbosity=2)

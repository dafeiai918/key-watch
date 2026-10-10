# -*- coding: utf-8 -*-
"""key-watch 测试：临时配置目录 + 本地假站点，不发外网请求"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import key_watch as kw  # noqa: E402

WEBHOOK_HITS = []


class _Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/v1/models"):
            self._json(200, {"data": [{"id": "m1"}, {"id": "m2"}]})
        elif self.path.endswith("/credit_grants"):
            self._json(200, {"total_available": 12.34})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        WEBHOOK_HITS.append(json.loads(self.rfile.read(length).decode("utf-8")) if length else {})
        self._json(200, {"ok": True})


class KeyWatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        cls.site_port = cls.site.server_address[1]
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._orig = (kw.CONFIG_PATH, kw.STATE_PATH)
        kw.CONFIG_PATH = base / "config.json"
        kw.STATE_PATH = base / "state.json"

    def tearDown(self):
        kw.CONFIG_PATH, kw.STATE_PATH = self._orig
        self.tmp.cleanup()

    def _add(self, name="A"):
        return kw.main(["add", "-n", name, "-u", f"http://127.0.0.1:{self.site_port}/v1",
                        "-k", "sk-test-abcdefgh"])

    def test_normalize(self):
        self.assertEqual(kw.normalize("https://a.com/v1"), ("https://a.com", "https://a.com/v1"))
        self.assertEqual(kw.normalize("https://a.com/"), ("https://a.com", "https://a.com/v1"))

    def test_add_duplicate(self):
        self.assertEqual(self._add(), 0)
        self.assertEqual(self._add(), 1)

    def test_add_list_remove(self):
        self._add("站点甲")
        cfg = kw.load_config()
        self.assertEqual(len(cfg["sites"]), 1)
        self.assertEqual(kw.main(["remove", "-n", "站点甲"]), 0)
        self.assertEqual(len(kw.load_config()["sites"]), 0)
        self.assertEqual(kw.main(["remove", "-n", "不存在"]), 1)

    def test_probe_site_ok_and_balance(self):
        site = {"name": "A", "base_url": f"http://127.0.0.1:{self.site_port}/v1",
                "api_key": "sk-test"}
        result = kw.probe_site(site, 10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["models"], 2)
        self.assertIsNotNone(result["balance"])
        self.assertAlmostEqual(result["balance"]["amount"], 12.34)

    def test_probe_site_down(self):
        site = {"name": "dead", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-test"}
        result = kw.probe_site(site, 3)
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_diff_alerts_down_and_up(self):
        cfg = {"low_balance_usd": 1.0}
        alerts = kw.diff_alerts([{"name": "A", "ok": False, "error": "超时", "balance": None}],
                                {"A": {"ok": True}}, cfg)
        self.assertEqual(len(alerts), 1)
        self.assertIn("站点异常", alerts[0])
        alerts = kw.diff_alerts([{"name": "A", "ok": True, "latency_ms": 120, "balance": None}],
                                {"A": {"ok": False}}, cfg)
        self.assertIn("恢复", alerts[0])

    def test_low_balance_alert_only_once(self):
        cfg = {"low_balance_usd": 1.0}
        cur = [{"name": "A", "ok": True, "latency_ms": 50, "balance": {"amount": 0.5}}]
        alerts = kw.diff_alerts(cur, {"A": {"ok": True, "balance": {"amount": 5.0}}}, cfg)
        self.assertTrue(any("余额不足" in a for a in alerts))
        alerts = kw.diff_alerts(cur, {"A": {"ok": True, "balance": {"amount": 0.4}}}, cfg)
        self.assertFalse(any("余额不足" in a for a in alerts))

    def test_notify_payloads(self):
        WEBHOOK_HITS.clear()
        url = f"http://127.0.0.1:{self.site_port}/hook"
        kw.notify(url, "测试告警")
        self.assertEqual(len(WEBHOOK_HITS), 1)
        self.assertEqual(WEBHOOK_HITS[0]["text"], "测试告警")

    def test_webhook_config(self):
        self.assertEqual(kw.main(["webhook", f"http://127.0.0.1:{self.site_port}/hook"]), 0)
        self.assertIn("hook", kw.load_config()["webhook"])

    def test_check_without_sites(self):
        self.assertEqual(kw.main(["check"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

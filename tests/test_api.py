import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from make_fixture import make

os.environ.setdefault("LEDGER_DB", "/definitely/missing.sqlite3")
from backend.app.main import app


class ObservatoryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fixture.sqlite3"
        make(self.db)
        os.environ["LEDGER_DB"] = str(self.db)
        self.client = TestClient(app)

    def get(self, *args, **kwargs):
        headers = kwargs.pop("headers", {})
        return self.client.get(*args, headers={"host": "127.0.0.1", **headers}, **kwargs)

    def tearDown(self): self.tmp.cleanup()

    def test_integer_totals_and_investment_is_included(self):
        res = self.get("/api/dashboard", params={"kind":"all"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["filtered_count"], 5)
        self.assertEqual(body["totals"]["income_cents"], "10005000")
        self.assertEqual(body["totals"]["expense_cents"], "3300")
        self.assertEqual(body["totals"]["income_yuan"], "100050.00")
        self.assertEqual(res.headers["cache-control"], "no-store")

    def test_tag_any_deduplicates_and_all_intersects(self):
        any_result = self.get("/api/dashboard", params=[("kind","all"),("tag","ai"),("tag","subscription"),("tag_mode","any")]).json()
        all_result = self.get("/api/dashboard", params=[("kind","all"),("tag","ai"),("tag","subscription"),("tag_mode","all")]).json()
        self.assertEqual(any_result["filtered_count"], 2)
        self.assertEqual(all_result["filtered_count"], 1)
        self.assertEqual(any_result["totals"]["expense_cents"], "1299")

    def test_closed_open_custom_date_and_validation(self):
        body = self.get("/api/dashboard", params={"kind":"custom","start":"2026-08-31","end":"2026-08-31"}).json()
        self.assertEqual(body["filtered_count"], 1)
        self.assertEqual(self.get("/api/dashboard", params={"kind":"custom","start":"2026-09-02","end":"2026-09-01"}).status_code,422)

    def test_loopback_and_limit_guards(self):
        self.assertEqual(self.get("/api/dashboard", headers={"host":"example.test"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"host":"localhost.evil"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"origin":"https://example.test"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", params={"limit":201}).status_code, 422)

    def test_version_changes_for_writes_and_initial_load_is_valid(self):
        query = {"kind": "month", "value": "2026-09"}
        first = self.get("/api/dashboard", params=query).json()["version"]
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE categories SET name='恶意 <img src=x>' WHERE id=1")
        conn.commit(); conn.close()
        second = self.get("/api/dashboard", params=query).json()["version"]
        self.assertNotEqual(first, second)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO transactions VALUES(6,'2026-09-03 00:00:00','支出',2,1,'')")
        conn.execute("INSERT INTO transaction_tags VALUES(6,1)")
        conn.commit(); conn.close()
        third = self.get("/api/dashboard", params=query).json()["version"]
        self.assertNotEqual(second, third)
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM transaction_tags WHERE transaction_id=6")
        conn.execute("DELETE FROM transactions WHERE id=6")
        conn.commit(); conn.close()
        self.assertNotEqual(third, self.get("/api/dashboard", params=query).json()["version"])

    def test_frontend_uses_node_construction_not_html_injection(self):
        source = (Path(__file__).parents[1] / "frontend" / "app.js").read_text()
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)

if __name__ == '__main__': unittest.main()

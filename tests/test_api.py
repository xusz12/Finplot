import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

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

    def test_tag_options_drilldown_investment_and_stable_cursor_pages(self):
        first = self.get("/api/dashboard", params={"kind": "all", "limit": 2}).json()
        self.assertEqual({tag["code"] for tag in first["tags"]}, {"ai", "subscription"})
        self.assertEqual(first["investment"]["income_cents"], "5000")
        self.assertEqual(first["investment"]["expense_cents"], "2000")
        self.assertEqual(first["investment"]["net_cents"], "3000")
        self.assertEqual(len(first["transactions"]), 2)
        self.assertTrue(first["next_cursor"])

        second = self.get("/api/dashboard", params={"kind": "all", "limit": 2, "cursor": first["next_cursor"]})
        self.assertEqual(second.status_code, 200)
        second_body = second.json()
        self.assertEqual(len(second_body["transactions"]), 2)
        self.assertTrue(first["transactions"][-1]["occurred_at"] > second_body["transactions"][0]["occurred_at"])

        group = self.get("/api/dashboard", params={"kind": "all", "group": "exp_life"}).json()
        category = self.get("/api/dashboard", params={"kind": "all", "category": "exp_food"}).json()
        self.assertEqual(group["filtered_count"], 3)
        self.assertEqual(category["filtered_count"], 2)
        self.assertEqual({row["category_code"] for row in category["transactions"]}, {"exp_food"})

    def test_cursor_and_version_conflict_after_dataset_change(self):
        first = self.get("/api/dashboard", params={"kind": "all", "limit": 2}).json()
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO transactions VALUES(6,'2026-09-03 00:00:00','支出',700,1,'')")
        conn.commit(); conn.close()
        stale_cursor = self.get("/api/dashboard", params={"kind": "all", "limit": 2, "cursor": first["next_cursor"]})
        stale_version = self.get("/api/dashboard", params={"kind": "all", "version": first["version"]})
        self.assertEqual(stale_cursor.status_code, 409)
        self.assertEqual(stale_version.status_code, 409)
        self.assertIn("version", stale_cursor.json()["detail"])

    def test_closed_open_custom_date_and_validation(self):
        body = self.get("/api/dashboard", params={"kind":"custom","start":"2026-08-31","end":"2026-08-31"}).json()
        self.assertEqual(body["filtered_count"], 1)
        self.assertEqual(self.get("/api/dashboard", params={"kind":"custom","start":"2026-09-02","end":"2026-09-01"}).status_code,422)

    def test_loopback_and_limit_guards(self):
        self.assertEqual(self.get("/api/dashboard", headers={"host":"example.test"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"host":"localhost.evil"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"origin":"https://example.test"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"origin":"https://localhost"}).status_code, 403)
        self.assertEqual(self.get("/api/dashboard", headers={"origin":"http://localhost:9999"}).status_code, 403)
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
        conn.execute("INSERT INTO transaction_tags(transaction_id,tag_id) VALUES(6,1)")
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

    def test_analytics_contract_has_same_snapshot_comparison_and_conservation(self):
        response = self.get("/api/analytics", params={
            "kind": "custom", "start": "2026-08-31", "end": "2026-09-02",
            "compare": "custom", "compare_start": "2024-02-29", "compare_end": "2024-02-29",
            "period_mode": "full", "grain": "day",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["contract_version"], "1.1")
        self.assertTrue(body["comparison"]["same_snapshot"])
        self.assertEqual(body["scope"]["end_exclusive"], "2026-09-03")
        self.assertEqual(body["comparison"]["compare"]["end_exclusive"], "2024-03-01")
        self.assertEqual(body["totals"]["income_cents"], "5000")
        self.assertEqual(body["totals"]["expense_cents"], "3299")
        self.assertEqual(body["daily_expense"]["effective_days"], 3)
        self.assertEqual(body["daily_expense"]["daily_expense_cents"], "1099.6667")
        self.assertEqual(sum(int(item["expense_cents"]) for item in body["periods"]), 3299)
        self.assertEqual(body["expense_calendar"]["total_expense_cents"], "1299")
        for direction, total_key in (("income", "income_cents"), ("expense", "expense_cents")):
            changes = body["category_changes_by_direction"][direction]
            self.assertEqual(
                sum(int(item["delta_cents"]) for item in changes),
                int(body["category_change_totals"][direction]["delta_cents"]),
            )
        nature_total_income = sum(int(item["income_cents"]) for item in body["nature_breakdown"])
        nature_total_expense = sum(int(item["expense_cents"]) for item in body["nature_breakdown"])
        self.assertEqual(nature_total_income, 5000)
        self.assertEqual(nature_total_expense, 3299)
        self.assertEqual(len(body["overview_12_months"]["months"]), 12)

    def test_analytics_zero_base_and_all_scope_are_explicit(self):
        all_body = self.get("/api/analytics", params={"kind": "all"}).json()
        self.assertFalse(all_body["comparison"]["available"])
        self.assertEqual(all_body["comparison"]["reason"], "all_scope_requires_custom_dates")
        self.assertEqual(all_body["totals"]["income_cents"], "10005000")
        no_base = self.get("/api/analytics", params={
            "kind": "month", "value": "2026-08", "compare": "previous", "period_mode": "full",
        }).json()
        self.assertIsNone(no_base["summary"]["delta"]["expense_cents"]["change_ratio"])
        self.assertEqual(no_base["summary"]["delta"]["expense_cents"]["change_ratio_reason"], "no_base")
        self.assertEqual(no_base["summary"]["delta"]["balance_cents"]["change_ratio_reason"], "negative_balance")

    def test_analytics_rejects_ambiguous_comparison(self):
        self.assertEqual(self.get("/api/analytics", params={
            "kind": "month", "value": "2026-08", "compare": "custom",
        }).status_code, 422)

    def test_analytics_year_ago_maps_leap_day_and_exclusive_end(self):
        cases = [
            ("2024-02-28", "2024-02-28", "2023-02-28", "2023-03-01", 1),
            ("2024-02-29", "2024-02-29", "2023-02-28", "2023-03-01", 1),
            ("2024-02-01", "2024-02-29", "2023-02-01", "2023-03-01", 28),
        ]
        for current_start, current_end, compare_start, compare_end, days in cases:
            with self.subTest(current_start=current_start, current_end=current_end):
                body = self.get("/api/analytics", params={
                    "kind": "custom", "start": current_start, "end": current_end,
                    "compare": "year_ago", "period_mode": "full", "grain": "day",
                }).json()
                self.assertEqual(body["comparison"]["compare"]["start"], compare_start)
                self.assertEqual(body["comparison"]["compare"]["end_exclusive"], compare_end)
                self.assertEqual(body["comparison"]["compare"]["days"], days)

    def test_analytics_elapsed_and_full_current_period_have_distinct_year_ago_ranges(self):
        query = {
            "kind": "month", "value": "2026-09", "compare": "year_ago", "grain": "month",
        }
        with patch("backend.app.main._analytics_today", return_value=date(2026, 9, 12)):
            elapsed = self.get("/api/analytics", params={**query, "period_mode": "elapsed"}).json()
            full = self.get("/api/analytics", params={**query, "period_mode": "full"}).json()
        self.assertEqual(elapsed["scope"]["end_exclusive"], "2026-09-13")
        self.assertEqual(elapsed["comparison"]["compare"]["start"], "2025-09-01")
        self.assertEqual(elapsed["comparison"]["compare"]["end_exclusive"], "2025-09-13")
        self.assertEqual(full["scope"]["end_exclusive"], "2026-10-01")
        self.assertEqual(full["comparison"]["compare"]["start"], "2025-09-01")
        self.assertEqual(full["comparison"]["compare"]["end_exclusive"], "2025-10-01")
        self.assertEqual(self.get("/api/analytics", params={
            "kind": "month", "value": "2026-08", "compare": "none",
            "compare_start": "2026-01-01", "compare_end": "2026-01-02",
        }).status_code, 422)

if __name__ == '__main__': unittest.main()

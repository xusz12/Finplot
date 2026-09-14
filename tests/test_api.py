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
        self.client = TestClient(app, client=("127.0.0.1", 50000))

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

    def test_tailscale_proxy_requires_exact_https_host_and_loopback_peer(self):
        public_host = "xmac-mini-1.tailef8d6d.ts.net"
        with patch.dict(os.environ, {"FINPLOT_PUBLIC_HOST": public_host}):
            proxy = TestClient(app, client=("127.0.0.1", 50001))
            forwarded = {
                "host": "127.0.0.1:8766",
                "x-forwarded-host": f"{public_host}:443",
                "x-forwarded-proto": "https",
                "origin": f"https://{public_host}/",
            }
            self.assertEqual(proxy.get("/api/version", headers=forwarded).status_code, 200)
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-for": "100.64.0.20"}).status_code,
                200,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={
                    "host": "127.0.0.1:8766",
                    "x-forwarded-proto": "https",
                    "origin": f"https://{public_host}",
                }).status_code,
                200,
            )

            # Tailscale may preserve the public Host instead of sending
            # X-Forwarded-Host; the HTTPS scheme is still required.
            preserved_host = {
                "host": public_host,
                "x-forwarded-proto": "https",
                "origin": f"https://{public_host}",
            }
            self.assertEqual(proxy.get("/api/version", headers=preserved_host).status_code, 200)
            self.assertEqual(
                proxy.get("/api/version", headers={**preserved_host, "origin": f"http://{public_host}"}).status_code,
                403,
            )

            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-host": "other.ts.net"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-proto": "http"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-host": f"{public_host},evil.test"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers=[
                    ("host", "127.0.0.1:8766"),
                    ("x-forwarded-host", f"{public_host}:443"),
                    ("x-forwarded-host", "evil.test"),
                    ("x-forwarded-proto", "https"),
                ]).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers=[
                    ("host", "127.0.0.1:8766"),
                    ("host", "evil.test"),
                    ("x-forwarded-host", f"{public_host}:443"),
                    ("x-forwarded-proto", "https"),
                ]).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers=[
                    ("host", "127.0.0.1:8766"),
                    ("x-forwarded-host", f"{public_host}:443"),
                    ("x-forwarded-proto", "https"),
                    ("origin", f"https://{public_host}"),
                    ("origin", "https://evil.test"),
                ]).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers=[
                    ("host", "127.0.0.1:8766"),
                    ("x-forwarded-host", f"{public_host}:443"),
                    ("x-forwarded-proto", "https"),
                    ("x-forwarded-proto", "http"),
                ]).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers=[
                    ("host", "127.0.0.1:8766"),
                    ("x-forwarded-host", f"{public_host}:443"),
                    ("x-forwarded-proto", "https"),
                    ("x-forwarded-for", "203.0.113.10"),
                    ("x-forwarded-for", "not-an-ip"),
                ]).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-for": "203.0.113.10, 198.51.100.20"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-for": "not-an-ip"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "forwarded": f"host={public_host};proto=https"}).status_code,
                403,
            )
            self.assertEqual(
                proxy.get("/api/version", headers={**forwarded, "x-forwarded-port": "443"}).status_code,
                403,
            )

            remote = TestClient(app, client=("203.0.113.10", 50002))
            self.assertEqual(remote.get("/api/version", headers=forwarded).status_code, 403)
            self.assertEqual(
                remote.get("/api/version", headers={
                    "host": "127.0.0.1:8766",
                    "x-forwarded-for": "203.0.113.10",
                }).status_code,
                403,
            )

    def test_public_proxy_access_is_disabled_without_explicit_host_config(self):
        with patch.dict(os.environ, {"FINPLOT_PUBLIC_HOST": ""}):
            self.assertEqual(self.get("/api/version").status_code, 200)
            self.assertEqual(
                self.client.get("/api/version", headers={
                    "host": "xmac-mini-1.tailef8d6d.ts.net",
                    "x-forwarded-proto": "https",
                }).status_code,
                403,
            )
        with patch.dict(os.environ, {"FINPLOT_PUBLIC_HOST": "evil.example"}):
            self.assertEqual(
                self.client.get("/api/version", headers={
                    "host": "evil.example",
                    "x-forwarded-proto": "https",
                    "origin": "https://evil.example",
                }).status_code,
                200,
            )
            self.assertEqual(
                self.client.get("/api/version", headers={
                    "host": "xmac-mini-1.tailef8d6d.ts.net",
                    "x-forwarded-host": "xmac-mini-1.tailef8d6d.ts.net",
                    "x-forwarded-proto": "https",
                    "origin": "https://xmac-mini-1.tailef8d6d.ts.net",
                }).status_code,
                403,
            )
        for invalid_host in ("*.ts.net", "a..b.example", "a,b.example", "a.example:443"):
            with self.subTest(invalid_host=invalid_host), patch.dict(os.environ, {"FINPLOT_PUBLIC_HOST": invalid_host}):
                self.assertEqual(
                    self.client.get("/api/version", headers={
                        "host": invalid_host,
                        "x-forwarded-proto": "https",
                    }).status_code,
                    403,
                )

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
        self.assertEqual(sum(item["income_count"] for item in body["periods"]), 1)
        self.assertEqual(sum(item["expense_count"] for item in body["periods"]), 2)
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

    def test_analytics_category_union_tag_filter_and_calendar_denominator(self):
        body = self.get("/api/analytics", params={
            "kind": "month", "value": "2026-01", "compare": "custom",
            "compare_start": "2024-02-29", "compare_end": "2024-02-29",
            "period_mode": "full", "grain": "day",
        }).json()
        codes = {(item["direction"], item["category_code"]): item for item in body["category_changes"]}
        self.assertIn(("收入", "inc_salary"), codes)
        self.assertIn(("支出", "exp_food"), codes)
        self.assertEqual(codes[("收入", "inc_salary")]["current_cents"], "10000000")
        self.assertEqual(codes[("支出", "exp_food")]["compare_cents"], "1")
        self.assertEqual(
            sum(int(item["delta_cents"]) for item in body["category_changes_by_direction"]["income"]),
            10000000,
        )
        self.assertEqual(
            sum(int(item["delta_cents"]) for item in body["category_changes_by_direction"]["expense"]),
            -1,
        )

        tagged = self.get("/api/analytics", params={
            "kind": "custom", "start": "2026-08-31", "end": "2026-09-02",
            "tag": "ai", "compare": "none", "period_mode": "full", "grain": "day",
        }).json()
        self.assertEqual(tagged["summary"]["current"]["transaction_count"], 2)
        self.assertEqual(tagged["daily_expense"]["effective_days"], 3)
        self.assertEqual(tagged["daily_expense"]["daily_expense_numerator_cents"], "1299")
        self.assertEqual(tagged["daily_expense"]["daily_expense_denominator_days"], 3)
        self.assertEqual(tagged["expense_calendar"]["total_expense_cents"], "1299")

    def test_expense_calendar_counts_only_expense_transactions(self):
        body = self.get("/api/analytics", params={
            "kind": "month", "value": "2026-09",
            "compare": "none", "period_mode": "full", "grain": "day",
        }).json()
        day = next(item for item in body["expense_calendar"]["days"]
                   if item["date"] == "2026-09-01")
        self.assertEqual(day["expense_cents"], "0")
        self.assertEqual(day["transaction_count"], 0)
        self.assertEqual(day["empty_label"], "无记录")

    def test_elapsed_custom_scope_preserves_explicit_future_end(self):
        with patch("backend.app.main._analytics_today", return_value=date(2026, 9, 13)):
            body = self.get("/api/analytics", params={
                "kind": "custom", "start": "2026-09-01", "end": "2026-09-30",
                "compare": "none", "period_mode": "elapsed", "grain": "day",
            }).json()
        self.assertEqual(body["scope"]["start"], "2026-09-01")
        self.assertEqual(body["scope"]["end_exclusive"], "2026-10-01")
        self.assertEqual(body["scope"]["requested_end_exclusive"], "2026-10-01")
        self.assertEqual(body["scope"]["days"], 30)
        self.assertFalse(body["scope"]["is_partial"])
        future_day = next(item for item in body["expense_calendar"]["days"]
                          if item["date"] == "2026-09-14")
        self.assertTrue(future_day["is_future"])
        self.assertIsNone(future_day["transaction_count"])

    def test_analytics_version_guard_and_future_periods(self):
        first = self.get("/api/analytics", params={
            "kind": "month", "value": "2026-09", "compare": "none",
        }).json()
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO transactions VALUES(6,'2026-09-03 00:00:00','支出',700,1,'')")
        conn.commit(); conn.close()
        stale = self.get("/api/analytics", params={
            "kind": "month", "value": "2026-09", "compare": "none", "version": first["version"],
        })
        self.assertEqual(stale.status_code, 409)
        with patch("backend.app.main._analytics_today", return_value=date(2026, 9, 12)):
            future = self.get("/api/analytics", params={
                "kind": "year", "value": "2026", "compare": "none", "period_mode": "full", "grain": "month",
            }).json()
        october = next(item for item in future["periods"] if item["key"] == "2026-10")
        self.assertTrue(october["is_future"])
        self.assertIsNone(october["income_cents"])
        self.assertIsNone(october["income_count"])

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

    def test_analytics_year_ago_preserves_complete_month_in_both_leap_directions(self):
        for month, compare_end, expected_days in (
            ("2024-02", "2023-03-01", 28),
            ("2025-02", "2024-03-01", 29),
        ):
            with self.subTest(month=month):
                body = self.get("/api/analytics", params={
                    "kind": "month", "value": month, "compare": "year_ago",
                    "period_mode": "full", "grain": "day",
                }).json()
                self.assertEqual(body["comparison"]["compare"]["start"], f"{int(month[:4]) - 1}-02-01")
                self.assertEqual(body["comparison"]["compare"]["end_exclusive"], compare_end)
                self.assertEqual(body["comparison"]["compare"]["days"], expected_days)

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
        self.assertTrue(full["overview_12_months"]["months"][-1]["is_partial"])
        self.assertEqual(self.get("/api/analytics", params={
            "kind": "month", "value": "2026-08", "compare": "none",
            "compare_start": "2026-01-01", "compare_end": "2026-01-02",
        }).status_code, 422)

if __name__ == '__main__': unittest.main()

# `/api/analytics` v1.1 contract

This is the backend/frontend handoff for task #9. It is served by the same
loopback-only FastAPI process as `/api/dashboard`; no frontend or database
schema changes are required.

## Query

The existing business filters remain unchanged: `kind`, `value`, `start`,
`end`, `direction`, `nature`, `tag`, `tag_mode`, `group`, and `category`.
`end` is an inclusive date in the request. The response always exposes the
actual half-open range as `start` and `end_exclusive`, in `Asia/Shanghai`.

Additional parameters:

| Parameter | Values/default | Meaning |
| --- | --- | --- |
| `compare` | `previous` (default), `year_ago`, `custom`, `none` | Comparison strategy |
| `compare_start`, `compare_end` | dates, required for `custom` | Inclusive comparison dates |
| `period_mode` | `elapsed` (default), `full` | Current in-progress natural period is clipped through today or kept complete |
| `grain` | `day`, `month`, `quarter`, `half`, `year` | Trend bucket; default is day for a month or short range, month otherwise |
| `top_n` | `10` (0 means all) | Number of categories in each direction before `other` |
| `version` / `if_version` | optional | Same snapshot/version guard as `/api/dashboard`; stale values return 409 |

`compare=custom` without both dates, an unknown grain/mode, or comparison
dates with another compare mode returns 422. A request using `all` with
`previous`/`year_ago` returns 200 with `comparison.available=false`; use
`custom` to compare an all-time range.

## Response shape

All fields whose name ends in `_cents` are decimal strings. Aggregate amounts
(`amount_cents`, `income_cents`, `expense_cents`, `balance_cents`, investment
totals, and their deltas) are integer cents. Average and per-day fields with a
decimal string use exact numerator/denominator siblings (for example,
`average_numerator_cents` + `average_denominator`, or
`daily_expense_numerator_cents` + `daily_expense_denominator_days`); the UI
must not pass those decimal strings to a BigInt integer-money formatter.
`share` is a finite JSON number in `[0, 1]` when its denominator is positive.
`balance_rate` is a signed finite number (it can be negative); growth
`change_ratio` is a finite signed number and may exceed 1. A zero base or an
unsupported negative-balance percentage returns `null` with a sibling
`*_reason`.
No ratio uses infinity. Future trend buckets are explicit `is_future=true`
with null measures; covered no-transaction buckets use zero strings.

Top-level fields:

| Field | Contents |
| --- | --- |
| `contract_version`, `version`, `instance_id`, `synced_at`, `read_only` | Contract/version and read-only evidence |
| `scope` | Current label, actual `start`, inclusive `end`, `end_exclusive`, day count, requested boundaries and coverage |
| `comparison` | Requested/effective mode, exact current/compare ranges, availability, same-snapshot flag and day difference |
| `summary` | `current`, `compare`, and `delta`; each summary has income, expense, balance, counts, four-property breakdown and investment realized gain/loss/net |
| `categories` | `income` and `expense` views: total, count, top items, and an `other` roll-up. Each item has amount, share, count, average and stable category code |
| `category_changes` | Full current/compare category union. `delta_cents` sums to the corresponding total delta for each direction; `category_change_totals` exposes that check |
| `trend` / `periods` | Current and comparison bucket arrays plus index-aligned pairs. Each bucket has exact range, income/expense/balance, cumulative balance, nature and investment values |
| `daily_expense` | Current expense divided by natural calendar days, with denominator and exact day range |
| `three_month_reference` | Three complete natural months before the selected month, weighted daily average, monthly average and a coverage warning |
| `overview_12_months` | Twelve natural months ending at the selected month; current unfinished month is marked `is_partial` |
| `expense_calendar` | One selected month of Shanghai natural days, per-day expense/count, future and in-scope flags |

`summary.current.nature` always contains `日常`, `投资`, `往来`, and `调整`.
Each property item exposes `income_cents`, `expense_cents`, `balance_cents`,
`transaction_count`, and `balance_rate`; the `日常` item is the daily-balance
rate used by the UI. `summary.current.investment` is realized investment gain,
loss and net only; investment is already included in the all-up totals.

## Synthetic response sample

The following is a shortened, non-financial sample produced from the isolated
`tests/make_fixture.py` database. It shows the names and types that the UI
needs; the actual endpoint includes all natural-day buckets and all four
nature items.

```json
{
  "contract_version": "1.1",
  "version": "<instance-id>:<logical-fingerprint>",
  "read_only": true,
  "scope": {
    "label": "2026-08",
    "start": "2026-08-01",
    "end": "2026-08-31",
    "end_exclusive": "2026-09-01",
    "days": 31,
    "timezone": "Asia/Shanghai"
  },
  "comparison": {
    "requested_mode": "previous",
    "mode": "previous",
    "available": true,
    "same_snapshot": true,
    "current": {"start": "2026-08-01", "end_exclusive": "2026-09-01"},
    "compare": {"start": "2026-07-01", "end_exclusive": "2026-08-01"}
  },
  "summary": {
    "current": {
      "transaction_count": 1,
      "income_cents": "0",
      "expense_cents": "1299",
      "balance_cents": "-1299",
      "investment": {
        "gain_cents": "0", "loss_cents": "0", "net_cents": "0"
      },
      "nature": [{
        "nature": "日常", "transaction_count": 1,
        "income_cents": "0", "expense_cents": "1299",
        "balance_cents": "-1299", "balance_rate": null,
        "balance_rate_reason": "no_base"
      }]
    },
    "compare": {"transaction_count": 0, "income_cents": "0", "expense_cents": "0", "balance_cents": "0"},
    "delta": {"expense_cents": {"current": "1299", "compare": "0", "delta_cents": "1299", "change_ratio": null, "change_ratio_reason": "no_base"}}
  },
  "categories": {
    "expense": {
      "total_cents": "1299", "transaction_count": 1, "top_n": 10,
      "items": [{
        "category_code": "exp_food", "category_name": "餐饮",
        "amount_cents": "1299", "transaction_count": 1,
        "average_cents": "1299.0000",
        "average_numerator_cents": "1299", "average_denominator": 1,
        "share": 1.0
      }],
      "other": {"category_code": "__other__", "amount_cents": "0", "is_other": true}
    }
  },
  "trend": {
    "grain": "day",
    "current": [{
      "key": "2026-08-31", "start": "2026-08-31", "end_exclusive": "2026-09-01",
      "income_cents": "0", "expense_cents": "1299", "balance_cents": "-1299",
      "cumulative_balance_cents": "-1299", "is_future": false
    }],
    "compare": []
  },
  "daily_expense": {
    "expense_cents": "1299", "effective_days": 31,
    "daily_expense_cents": "41.9032",
    "daily_expense_numerator_cents": "1299",
    "daily_expense_denominator_days": 31,
    "denominator": "calendar_days"
  },
  "expense_calendar": {
    "month": "2026-08", "total_expense_cents": "1299",
    "days": [{"date": "2026-08-31", "expense_cents": "1299", "transaction_count": 1, "in_scope": true}]
  }
}
```

The detail table remains `/api/dashboard`: a chart click passes the selected
bucket's actual `start`/`end_exclusive` as the dashboard `kind=custom` range.
For a comparison click it passes `comparison.compare`, never the current
range. The supplied `version` prevents pagination from mixing snapshots.

# Browser and concurrent-refresh performance evidence

Run date: 2026-09-12. Machine: MACMINI, arm64, macOS 26.6.2, Python 3.14.4. All writes in this report used isolated synthetic SQLite databases under `/tmp`; the real ledger was not opened by these tests.

## Browser render timing

The final frontend was served from `http://127.0.0.1:8772/` with a 10,000-transaction synthetic fixture (`fixture_sha256=1b88a26541fa6eb3fff89b24d18257926e5d939000530810abee18bbc79d97a3`). Chrome native accessibility state was used to drive the same page controls and read the page's diagnostic text. Each value is the browser-side elapsed time from the action's `fetch` start to synchronous DOM rendering completion, measured with `performance.now()` in `frontend/app.js`; it is not an API-only timing.

| Action | n | Samples (ms) | min | p95 | max |
| --- | ---: | --- | ---: | ---: | ---: |
| Initial page | 20 | 114, 118.8, 119.1, 123, 126.2, 124.8, 123.8, 124.3, 121, 122.8, 117.8, 119.4, 126.4, 123.6, 119.5, 112.6, 119.4, 124.1, 125.5, 121.2 | 112.6 | 126.2 | 126.4 |
| AI tag filter | 20 | 57.7, 86.1, 59.3, 84.4, 100.6, 84.9, 61.3, 83, 60.5, 83.2, 60.6, 80.8, 62.8, 67.5, 62.5, 82.7, 60.2, 84.4, 101, 86.2 | 57.7 | 100.6 | 101.0 |
| Category-group drill-down | 20 | 83.6, 87.9, 82.7, 83.5, 83, 81, 82.3, 87.9, 77.3, 85.8, 79.8, 89.1, 84.3, 105.4, 83.6, 87, 80.5, 85.4, 81, 88.8 | 77.3 | 89.1 | 105.4 |

The page's diagnostic also records a wall-clock render epoch after DOM update. Chrome/this harness does not expose a reliable compositor-paint timestamp, so compositor paint remains unverified; the measurements above are browser-side render-complete timings and do not substitute API timings.

## Concurrent version polling versus no polling

Command: `.venv/bin/python tests/benchmark.py`. The benchmark uses 10,000 synthetic transactions, 100 WAL commits per condition, and a background `TestClient` thread that continuously requests `/api/version` during the polling condition. A 20 ms inter-commit pause is outside each measured commit interval so the reader load overlaps the full 100-commit run.

- No polling: max 0.77 ms, p95 0.63 ms, commit errors 0, SQLite lock errors 0.
- Continuous polling: max 6.50 ms, p95 4.98 ms, commit errors 0, SQLite lock errors 0; 131 version probes, probe errors 0.
- p95 increment: 4.35 ms (below the 50 ms target).
- CPU sample: no-polling run 0.045 s; polling run 2.771 s. Peak RSS 61.88 MB.
- API-only reference timings from the same run: initial max 51.52 ms (p95 36.58), filter max 32.36 ms (p95 32.19), drill max 30.93 ms (p95 30.88). These are kept separate from the browser table above.

## Synthetic commit-to-page-update samples

The page was served from `http://127.0.0.1:8771/` against a fresh 10,000-transaction fixture. An external synthetic writer committed 20 rows at 3-second intervals. The final page diagnostic reported exactly 20 automatic refreshes and retained all 20 render epochs. Each latency is `browser_render_epoch - writer_commit_epoch` in milliseconds; pairing is by refresh order.

| # | Commit epoch | Render epoch | Delay (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 1789144206068 | 1789144207272 | 1204 |
| 2 | 1789144209074 | 1789144209286 | 212 |
| 3 | 1789144212080 | 1789144213290 | 1210 |
| 4 | 1789144215085 | 1789144215282 | 197 |
| 5 | 1789144218091 | 1789144219285 | 1194 |
| 6 | 1789144221094 | 1789144221239 | 145 |
| 7 | 1789144224099 | 1789144225240 | 1141 |
| 8 | 1789144227101 | 1789144227242 | 141 |
| 9 | 1789144230107 | 1789144231287 | 1180 |
| 10 | 1789144233113 | 1789144233290 | 177 |
| 11 | 1789144236119 | 1789144237297 | 1178 |
| 12 | 1789144239124 | 1789144239297 | 173 |
| 13 | 1789144242130 | 1789144243287 | 1157 |
| 14 | 1789144245134 | 1789144245288 | 154 |
| 15 | 1789144248140 | 1789144249283 | 1143 |
| 16 | 1789144251142 | 1789144251289 | 147 |
| 17 | 1789144254148 | 1789144255288 | 1140 |
| 18 | 1789144257154 | 1789144257287 | 133 |
| 19 | 1789144260159 | 1789144261296 | 1137 |
| 20 | 1789144263165 | 1789144263278 | 113 |

Summary: min 113 ms, median 674.5 ms, p95 1204 ms, max 1210 ms. The alternating short/long delays reflect the 2-second browser version-poll interval; all 20 commits produced a corresponding page refresh.

Note: the fixture hash for the benchmark and browser seed is `1b88a26541fa6eb3fff89b24d18257926e5d939000530810abee18bbc79d97a3`.

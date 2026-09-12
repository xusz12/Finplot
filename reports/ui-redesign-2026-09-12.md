# UI redesign — task #6

Owner: 老殷. Baseline: 119f49c. User request: replace the rough initial UI with a deliberately designed interface.

## Design and implementation
- Light paper background, sage green navigation and controls, deep green net balance card, muted sand expense series; consistent spacing, borders, typography, and tabular numerals.
- Persistent desktop section links; stacked mobile navigation and two-column metric cards below 700px.
- Grouped scope and tag controls; reset action; named category breadcrumb and clear drill action.
- Separate income/expense daily SVG bars with shared scale, exact amounts in accessible labels/title, scrollable long daily ranges. No added dependency/CDN.
- Readable transaction hierarchy, direction badges, right-aligned amounts; cumulative pagination count and zero-result count fixed.
- Diagnostics collapsed; sync clock explicitly renders hour/minute/second instead of fractional seconds alone.
- Existing read-only API, database path, refresh/version behavior and security policy unchanged.

## Validation
- Existing isolated API/security suite: 8/8 passed after formatting.
- Node syntax check and git diff whitespace check passed.
- Native Chrome on synthetic 8765: new page rendered; desktop screenshot visually inspected. Income 51.23, expense 20.40, balance 30.83, count 43, investment 31.23.
- Click investment category: breadcrumb shows 工作 / 投资收益, count 2, expense 0, balance 51.23. Clear drill button returned to overview.
- Confirmed sync time visibly renders HH:MM:SS.
- Existing real instance 8766 serves updated page with HTTP 200; no backend/DB mutation is part of this change.
- Mobile visual verification and extended browser interaction are pending: after attempting DevTools device mode the native Chrome surface started returning only window titles with no AX contents or screenshot. Do not treat CSS breakpoint inspection as browser verification.

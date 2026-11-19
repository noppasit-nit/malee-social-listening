# Malee Social Listening Dashboard (mock-up)

Live: https://piyaster11.github.io/malee-social-listening/

Mock-up of a social listening dashboard for Malee Group and key competitors (Tipco, Doi Kham, if, Cocomax, Chaokoh).
All 136 mentions are real public posts/pages with source URLs, collected 19 Nov 2026 (Bangkok time, UTC+7) — no sample data.
Sentiment, topics and complaint flags are auto-classified (not human-verified). Complaint status/owner are stored in the browser (localStorage) only.

Files: `docs/index.html` (dashboard, Chart.js via CDN), `docs/data.json`, `docs/mentions.csv`.

## QA
`python3 tests/regression.py [URL]` (Playwright + Chrome) checks filters, Thai search, date edges, empty state, CSV BOM and formula guard, saved status, corrupt localStorage, XSS escaping, labels, 360 px overflow, chart totals vs data.json.
Also covers recency labels (NEW ≤7d · Recent 8–30d · This year 31–365d · Old >1y · Date unknown), recency chips, sort toggle and `?today=YYYY-MM-DD` override for testing.

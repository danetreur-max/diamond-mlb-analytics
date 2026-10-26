# Resume and interview notes

## Resume bullet

Built a full-stack MLB research platform with React, TypeScript, FastAPI and SQLAlchemy, ingesting 16,740 games and 522,997 player-game records; implemented recoverable imports, source-tracked contract comparisons and historically evaluated performance benchmarks.

## Supporting bullet

Added automated backend and frontend checks, a read-only portfolio demo, browser journey tests and GitHub Actions configuration; documented data provenance, benchmark errors and deployment tradeoffs.

Do not describe configured CI as passing until the GitHub run succeeds. Do not claim the static public demo hosts the Python API. Avoid calling the weighted baseline machine learning, a salary valuation model or a validated published projection.

## Interview walkthrough

1. Start with the problem: users need context connecting performance, money and expectations.
2. Show a player brief, change cost scope and inspect source dates.
3. Explain the atomic per-game import, exact IDs and correction handling.
4. Show retrospective benchmark error, then explain survivorship bias and missing data.
5. Discuss SQLite simplicity, the single-worker limit and what you would change for a multi-user production service.
6. Open actual tests and local timing evidence. Explain why local latency is not a load-test result.

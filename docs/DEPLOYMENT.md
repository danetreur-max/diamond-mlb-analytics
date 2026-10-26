# Deployment

## Public portfolio demo

`portfolio-demo/` is self-contained static HTML/CSS/JavaScript plus an explicitly dated public-data snapshot. Serve that directory with any static host. No environment variables, database, API credentials or write permissions are required. The Sites-hosted copy is managed separately; it is not the full Python application.

Refresh intentionally from the local complete app with `python scripts/export_demo.py`. Inspect the snapshot and source dates before republishing. The export uses only local API GET requests. Its HTTP timing report is a small sequential local measurement, not a load test.

## Complete app: single-instance read-only container

```sh
docker build -t diamond-demo .
docker volume create diamond-data
docker run --rm -p 127.0.0.1:8000:8000 -v diamond-data:/data diamond-demo
```

The image defaults to `MLB_PUBLIC_DEMO=1` and `MLB_START_WORKER=0`. All non-GET/HEAD/OPTIONS HTTP methods are rejected before route handlers. The frontend and API share an origin. Health endpoint: `/api/health`. Use one worker and one instance with SQLite. A new data volume starts empty; prepare a separate demo-only database locally and transfer it through your host's private storage tooling. Do not expose a writable development instance or publish raw database files in Git.

Place HTTPS and rate limits at the hosting proxy. Keep the volume private, monitor disk growth from caches and take backups before schema changes. This initial deployment has no authenticated administration or migration baseline. Never enable mutations on an internet-facing instance without those additions. Docker is not installed in the local verification environment. GitHub Actions successfully built the image and checked its frontend, health endpoint, read-only configuration and mutation rejection on October 26, 2026.

## GitHub source and updates

[Public source repository](https://github.com/danetreur-max/diamond-mlb-analytics). The demo URL and project topics are configured in About, and the repository is pinned to the owner profile.

Clone the repository for future changes, inspect `git status`, run `python scripts/scan_secrets.py`, and stage reviewed source only. The ignore file excludes credentials, runtime databases, raw source downloads and logs. GitHub Actions verifies backend tests, frontend checks/build, desktop/mobile demo journeys and the production container. Check the run associated with your code commit before making verification claims.

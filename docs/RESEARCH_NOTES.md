# Diamond — MLB Explorer

React + TypeScript + Vite frontend; FastAPI + SQLAlchemy backend. PostgreSQL is supported via the included Docker Compose database. SQLite is the zero-setup local default. No salary data or fabricated sample records.

## Run locally

Requires Python 3.11+ and Node 20.19+ (or Node 22.12+).

From `backend`:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, from `frontend`:

```powershell
npm install
npm run dev
```

Open http://127.0.0.1:5173. API docs: http://127.0.0.1:8000/docs.

Choose **Imports**, select up to 31 days, and click **Start import**. Try June 1–2, 2025. Completed runs remain in the history. Refresh the explorer after imports finish.

### PostgreSQL

From the repository root, run `docker compose up -d`. Before starting the backend, set:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://mlb:mlb_local_only@127.0.0.1:5432/mlb'
```

Switching databases does not migrate existing SQLite data; reimport it. Tables are created on startup. This initial version uses schema creation, not Alembic migrations; add a migration baseline before changing deployed schemas.

## Models and ingestion guarantees

- MLB IDs identify teams, players, and games. Batting and pitching lines use `(game_id, player_id)` as a composite primary key. Historical team ID belongs to each line.
- Counting statistics live in JSON columns so MLB fields can evolve without dropping data. Innings are stored separately as integer outs. Rate statistics are excluded from saved stat counts.
- Local `/api/players` totals reflect **only imported games**, filtered by season and game type. The enhanced UI uses `/api/explore` official MLB season snapshots for summaries, standings and leaderboards; imported logs, recent form and home/away splits are explicitly labeled separately. Profiles retain historical team IDs on game lines.
- A separate worker processes a persistent database queue. A local OS file lock prevents duplicate workers; run API and worker from the same backend directory. This is a single-machine design, not a distributed queue.
- Each game's schedule and both sets of player lines commit atomically. Reimporting replaces corrected or removed lines, without duplicates. Games that fail are reported individually; other games continue.
- Network timeouts, 429s, and server failures retry up to five attempts with backoff. Import requests are bounded to 31 days and the API allows up to ten active/queued runs. Requests are paced between games.
- On restart, interrupted runs become failed with an explanatory message; queued runs resume. Retry interrupted/partial ranges from the UI. Successfully saved games remain intact.
- The latest raw box score per game is retained in `source_payloads`, with fetch time. It is not an append-only audit archive. Team metadata refreshes each run; scheduled games import without box scores.
- Import only data you are permitted to collect under MLB's terms: https://gdx.mlb.com/components/copyright.txt.

## REST endpoints

### Full-season backfills

From `backend`, queue long ranges as durable monthly jobs (each at most 31 days):

```powershell
.venv\Scripts\python -m app.backfill --start 2025-01-01 --end 2026-10-10 --manifest ../backfill-2025-2026.json
```

Run the API or `python -m app.worker` so queued jobs are processed. The dedicated bulk command can queue more than the interactive API's ten-job limit. Its manifest records expected unique game IDs and created run IDs; it refuses to overwrite an existing manifest to prevent accidental duplicate queueing. Progress is visible in **Imports**. Keep the computer running while the worker processes jobs. Spring/exhibition opponents outside MLB are added to the team directory when needed. Final and live games are fetched as of their processing time; today's live games can be refreshed with another import later.

`GET /api/health`, `/api/summary`, `/api/teams`, `/api/players?q=`, `/api/players/{id}?season=2025&game_type=R`, `/api/games?start_date=&end_date=&team_id=`, `/api/games/{id}`, `/api/imports`; `POST /api/imports` accepts `{"start_date":"2025-06-01","end_date":"2025-06-02"}` and returns HTTP 202. Games and players support `offset` and `limit` (max 200).

## Verification

From backend: `python -m pytest`. From frontend: `npm run build`.

## Scope

Intended for local use. API binds to loopback and frontend uses a development proxy. There is no authentication. Before hosting, add authentication, a production frontend server, migrations, and a distributed job runner if using multiple machines. To stop the independently running local worker, stop its `python -m app.worker` process; it intentionally survives API restarts. All other source files are under this project directory.

## Enhanced explorer

- **Teams:** sortable W/L, win percentage, division/league/MLB ranks, games back, run differential, last ten and streak. Filter by league and division, then click a team.
- **Team details:** official overview and batting/pitching totals; active and 40-man roster snapshots by date with historical age, position, handedness, status and season statistics; complete schedule/results with game links.
- **Players and leaderboards:** batting/pitching tables with sortable statistics, team/position/name filters, paging, tie-aware rankings and MLB's official qualified player pool for regular-season leaderboards. Season-team filters use MLB's latest listed team; displayed totals include all clubs.
- **Player details:** profile, official season summary, imported game log, weighted 7/15/30-calendar-day recent form with a per-game chart, official season history and imported home/away splits. Two-way players can switch disciplines. Recent windows end at the latest imported appearance and disclose their sample size.
- **Data provenance:** official snapshots retain fetch timestamps in the existing `source_payloads` table (keys prefixed `explorer:`). They refresh after 30 minutes; upstream failure can serve a labeled stale snapshot. Local coverage compares played schedule IDs to imported box scores. Missing games are excluded rather than treated as zero performance. Traded-player history prefers the MLB all-club total, otherwise sums stints and recalculates rates from counts. Innings use integer outs.

New GET endpoints under `/api/explore`: `/standings?season=`, `/coverage?season=&game_type=`, `/teams/{id}?season=&game_type=`, `/teams/{id}/roster?season=&roster_type=active|40Man&as_of=YYYY-MM-DD`, `/players?season=&game_type=&discipline=batting|pitching&qualified=&q=&team_id=&position=&sort=&direction=&offset=&limit=`, `/players/{id}?season=&game_type=&window=7|15|30`, and `/games/{id}`. Game detail can fetch a cached upstream box score for a schedule game not yet imported. No schema migration is required.

Verification: 11 backend tests passed, including weighted rate calculations, traded-player aggregation, ranking/qualification and historical roster dates; TypeScript and production build passed. Browser checks covered team-to-roster-to-player navigation, leaderboards/search, pitching window changes, history and splits. The durable full-season worker continues independently.
## Comparison and personal exploration

- **Compare:** add 2–4 players using Compare in the directory/profile or search on the comparison page. Switch batting/pitching, compare official season totals, inspect differences from the first player, and see the best rate values highlighted. Missing values stay blank; count totals are not treated as performance ratings.
- **Year changes:** compares adjacent seasons using AVG/OBP/SLG/OPS or ERA/WHIP, with a sample minimum required in both years. Lower ERA/WHIP counts as improvement. Uses official all-club season totals, not imported partial logs.
- **Player filters:** minimum PA or IP, age range, and pitchers classified by whether at least half their appearances were starts. Filters apply before ranking and pagination. IP minimum inputs use whole innings; displayed IP still uses baseball outs notation.
- **Rolling trends:** Recent form includes weighted rolling 7/15/30-calendar-day AVG/OPS or ERA/WHIP over imported appearances. Rates use summed counts and integer outs, not averages of per-game rates. Missing games are excluded. Hover chart points for sample size and exact value.
- **Favorites:** save players and teams from their profiles to a browser-local dashboard with recent player form, recent team results, and upcoming schedule entries. Favorites, comparison selections, global season/competition, directory filters/sorts and visible metric columns persist in browser storage; they do not sync between devices.
- **Wide tables:** player identity stays visible during horizontal scrolling. Choose columns controls the displayed metrics per discipline, with Show all to restore defaults.
- **Freshness:** a latest imported game date is shown separately from official snapshot timestamps. Refresh new games + corrections queues a bounded job with a seven-day overlap to revisit corrections, catching up at most 31 days per request. Existing active jobs block duplicate refreshes; large gaps require another refresh after completion. A latest game date alone does not prove complete historical coverage. This refresh updates local game data; MLB/Statcast summary enrichment remains separate.

Additional endpoints: GET `/api/workspace/changes`, GET `/api/workspace/freshness`, POST `/api/workspace/refresh`. The existing explorer player endpoint accepts `min_pa`, `min_ip`, `min_age`, `max_age` and `pitcher_role=all|starter|reliever`.

Validation: 35 backend tests passed, including filter-before-pagination behavior, year changes, and duplicate-refresh blocking. `node test_rolling.cjs` checks weighted frontend rolling rates and zero samples. TypeScript and Vite production build passed. Browser checks verified comparisons, sample/age filters, pitcher year changes, rolling controls, favorite dashboard and preference persistence after reload. Prior summary and Statcast pages remain available.

## Restarting the local app
Run ./Start.ps1 from the project directory. It starts missing API/frontend services as independent hidden processes, verifies readiness, and keeps diagnostics under logs/. It is safe to rerun when services are already running. This does not configure automatic startup after a computer restart.


### Explorer refinements
Player tables offer Beginner, Advanced, power/pitching, and custom column presets. Advanced rates derive from the selected official MLB totals: ISO, BABIP, K%, BB%, pitching K-BB%, K/9, BB/9, HR/9 and K/BB. Missing inputs and undefined denominators remain unavailable. These are not park-adjusted or league-adjusted metrics.

Year changes includes batting and pitching totals plus the derived rates. Sample minimums apply to both years, with an optional regular-season qualification filter. Rate improvement direction depends on discipline; counting stats and BABIP use neutral increase/decrease labels. Percentage-rate deltas are percentage points; badges show relative percent change. Innings are calculated in outs and displayed in baseball notation.

Player profiles include official left/right matchup splits. Freshness shows selected competition coverage, latest imported game, missing boxes and last successful import. Request failures distinguish local server and upstream errors; cached results remain available for the same selection, with retry controls.

Validation: 41 backend tests, frontend change-label and error-handling checks, TypeScript and production build; live browser checks of ISO, K-BB% and innings changes.

### Pattern discovery
Year changes now includes descriptive multi-stat patterns with expandable evidence and explicit fixed screening thresholds. These summarize associations, not causal explanations, predictions, or statistical significance. Missing paired metrics never count as stable values. Workload differences of at least 25% and small samples are flagged.

Explore this change opens imported Statcast comparisons (regular season only) and first-N-complete-appearance comparisons. Equal-appearance comparisons require complete imported coverage in both years and display actual PA/IP and date ranges; they do not prorate appearances or imply equal opportunities. Favorites has per-player/year/competition review baselines stored locally, with explicit reset and no stale-data overwrite. Statcast absence remains unavailable.

Validation: 48 backend tests, frontend pairing/role/missing-value/review-threshold checks, TypeScript and production build. Live browser checks cover a season pattern, Savant evidence, and first 30 appearances.

### Team contract analysis
Open a team, then Contracts (regular season only). The dashboard joins its MLB 40-man snapshot with source-tracked per-player/team/season financial records. Imported players outside that snapshot stay visible. Salary and WAR are full player-season figures across clubs; known-salary totals are partial and are not team-allocated payroll. Provider filters prevent combining WAR definitions in the chart.

The CSV template is downloadable in the dashboard. Preview is read-only; Save validates again and atomically upserts the full record while preserving each import in contract_analysis_imports. Missing columns/cells clear previous values for that player/team/season. Invalid files, duplicate keys, unknown MLB IDs, non-finite values, negative salaries and missing provenance reject the entire batch. No example salaries or invented WAR/projections are seeded.

Fields: season, team_id, player_id; salary, aav, tax_payroll (independent USD amounts); remaining_years, remaining_guarantee; contract_status (pre-arbitration, arbitration, free-agent, extension, other); salary_source, contract_as_of; actual_war, war_provider, war_source, actual_as_of; projected_war, projection_source, projection_date; completed_team_games and total_team_games at the WAR snapshot; optional actual_pa, projected_pa, actual_outs, projected_outs and notes. All dates use YYYY-MM-DD. WAR must be total player-season WAR and use the same named provider for actual and projection. No name matching is performed.

Expected WAR to date = projected full-season WAR * completed_team_games / total_team_games. This is explicitly a linear pace benchmark, not a forecast or a salary-implied expectation. Availability context compares snapshot PA/IP with projected workload at the same game fraction; it does not infer injury or missed days. Cost per WAR divides full annual salary by positive actual WAR; it is undefined at zero or negative WAR and is not prorated cash spending.

Contract data must be imported from an appropriate source. RosterResource explains annual salary, AAV and tax payroll: https://www.fangraphs.com/roster-resource/payroll/braves?season=2026 . Baseball Reference documents WAR and links downloadable WAR data: https://www.baseball-reference.com/about/war_explained.shtml . These sources are not automatically scraped by this feature; dated projections require their own source.

Validation: 57 backend tests (including pacing, zero/negative WAR, missing provenance, CSV rejection, ID validation, preview/save and historical audit), TypeScript/production build, and live roster and CSV error checks.

### Public financial sources
2025 and 2026 are populated from all 30 FanGraphs RosterResource public payroll pages and Baseball Reference's openly published batting/pitching WAR files. Source MLBAMID/mlb_ID fields join exact MLB identities. No fuzzy name matching is used. New MLB identities are verified through the official people endpoint. The initial successful import contains 1,898 player/team/season financial records, 1,789 with matched bWAR; all 60 payroll pages succeeded and no IDs remain unmatched.

Use Refresh public financial data in a team's Contracts tab to refresh both seasons across the league. It runs independently in a hidden background process, rejects duplicate launches, displays progress, and retains prior records when individual source pages fail. Cached source pages and WAR files live in backend/financial-source-cache; the button forces fresh downloads. The repeatable CLI is `.venv/Scripts/python.exe -m app.financial_sources --years 2025 2026` (optional --force). Import reports and provenance are stored in source_payloads and contract_analysis_imports.

Salary basis is full-season, team-payroll, or estimated. Prorated/split/transferred/retained or off-roster amounts are conservatively marked team-payroll. Cost per full-player-season WAR is omitted for allocated or estimated amounts. Source notes remain visible. AAV and tax payroll use separate source fields. Guaranteed future-season salary sums only listed GUARANTEED salary after the selected season, excludes options, buyouts, current-year unpaid salary and unlisted deferrals, and is only populated for the current-year source snapshot; historical pages cannot reconstruct past remaining commitments.

Actual bWAR sums each batting and pitching team stint once, skips all-team totals, and does not fill absent WAR with zero. WAR snapshot dates represent retrieval snapshots; historical years use year-end scope with retrieval notes. Existing analyst projections and their WAR provider are preserved. A changed actual WAR snapshot invalidates snapshot-matched game counts. Dated projections are never generated from salary or current performance.

Validation added: source season matching, future contracts versus options, historical guarantee treatment, prorated/estimated classification, additive WAR stints, projection-provider preservation, file locks and duplicate refresh launches. Full backend suite passed 63 tests before the final two refresh-control checks; all 8 source-adapter checks passed afterward. TypeScript and production build passed. Live Atlanta verification: 41 financial records and 37 paired WAR records among 52 roster/imported player rows.

### Free contract research and logos
All 30 current MLB cap logos are stored locally in frontend/public/team-logos, sourced from MLB static assets. Team standings, team profiles, schedules, game club headings and saved-team cards use them, with abbreviation fallbacks if an asset fails.

Contracts now supports full-player-season or club-only cost/production views. Club WAR adds only the matching Baseball Reference team stints, and club salary charge is the source-reported RosterResource amount, never allocated by appearances. These charges may include retained/prorated/off-roster obligations and are not verified cash spending. Annual benchmark gaps remain full-player-season comparisons, regardless of club view.

Free historical benchmark v1 weights available previous seasons 5:3:1; missing years are omitted rather than assumed zero. It excludes the evaluation season. Benchmark pace is scaled linearly using selected-club final regular-season games at the WAR snapshot over 162 for 2025/2026. It is a retrospective reconstruction from retrieved historical bWAR, not an archived preseason projection, Marcel implementation, or validated forecast. Limited history, input cutoff and reconstruction date are visible.

Publicly readable current-year Steamer tables are imported separately as reference forecasts. Original preseason publication dates are not established. Steamer/fWAR is never subtracted from actual bWAR; historical paid archives are not accessed. The dashboard preserves dated analyst projections as an independent import path. Forecast references, historical benchmarks and actuals remain separate.

Peer context requires at least five other players with the same position group, contract status and WAR provider, season age within three years and 0.5–2x workload. Only confirmed full-season salary records are used; conflicting duplicate player records are excluded. Rates use WAR/600 PA or WAR/180 IP. Position comes from the current MLB directory, season age from the WAR source. Peer medians/percentiles are descriptive, not recommended prices.

Player details load official team transaction timelines on demand. Observed IL calendar intervals may be partial; retroactive or missing boundaries are flagged, rehab does not end IL time, and a trade out closes the club interval with an incompleteness flag. Calendar days are not games missed. No medical cause is inferred.

Quality flags identify missing contract/WAR/club values, estimates, allocated payroll, stale snapshots, differing playing-time snapshots and forecast-date/provider mismatches. Source fallbacks retain verified cached pages with their original dates. --cached-only rebuilds finance/research from downloaded sources without network calls. Local snapshot dates avoid treating UTC midnight as a future date in America/Los_Angeles.

Validation: full 70-test backend suite passed; the added trade-boundary check and all six contract-intelligence tests passed afterward. TypeScript and production build passed. Live Austin Riley checks verified club WAR/charge, historical inputs, public forecast reference, 15 matched peers, incomplete transaction-boundary handling and club-scope UI switching.

Decision research: Team Contracts now includes position-group reviews with coverage counts, rolling benchmark errors, player briefs, annual stored contract snapshots, named peer evidence, browser-local saved reviews and JSON exports. Historical validation requires prior history and target-year MLB WAR; it excludes players without target-year records and uses retrospectively revised data. MAE is not an individual confidence interval. Contract history is stored annual snapshots, not an original signed payment schedule. Saved reviews are player/team/year scoped, local to this browser, and update on revisit without background alerts.

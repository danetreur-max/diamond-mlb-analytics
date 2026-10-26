# Full backfill

## Completed publication

On October 11, 2026, the feature was merged into `main` with the existing standings, roster, rankings and header-help changes preserved. The backfill completed and was published transactionally to the primary database: 15,761 played final games, 6,779 players, 80,007 MLB summary rows and 45,507 Savant rows. Five explicitly cancelled fixtures have abstract state Final in MLB and are reported separately, without requiring nonexistent player stats. All 13,558 regular/postseason player fetches and 35 Savant exports passed count audits.

Independent verification found no missing scoped games, no lost preexisting primary games, no foreign-key violations, and SQLite quick_check returned ok. A timestamped primary backup was retained. Main runs on frontend port 5173 and API port 8000 using its own published database and manifest, with source overrides removed. The normal import worker is restored.


## Scope

- MLB schedules and box scores: January 1, 2020 through October 10, 2026; regular season and Wild Card, Division Series, Championship Series and World Series games.
- MLB source summaries: all available MLB seasons and career totals for every player in the database, including players discovered during the game backfill. Hitting, pitching and fielding are fetched separately for regular season and combined postseason. These summaries are as of retrieval, not point-in-time predictions or reconstructed historical snapshots.
- Baseball Savant regular-season leaderboards, 2020–2026: expected outcomes for hitters/pitchers, contact quality for hitters/pitchers, and pitch arsenals. Minimum filters are set to zero; source coverage and export-specific eligibility still apply. This is 35 seasonal exports, not a pitch-level archive.
- Baseball Reference is not ingested. It remains a possible enrichment source subject to permitted access.

The initial MLB manifest identified 15,765 final games and one in-progress game. Of those final games, 1,115 already had matching game metadata, raw payloads and batting/pitching lines for both teams in this checkout; 14,650 required ingestion. Counts can change when the schedule is refreshed before the final audit.

## Isolation from main

The primary branch's completed standings, roster, ranking, and header-help changes have been preserved in a checkpoint and merged into this feature branch. The backfill keeps its independent SQLite snapshot and API on port 8001, with the generic import worker disabled. Copied pending/running queue entries are marked `snapshot` only in the feature database. The dedicated backfill process operates independently of the primary database and its existing worker. Primary data must be reconciled before publishing the completed backfill.

## Run and resume

From backend, with the required dependencies installed:

```powershell
python -m app.full_backfill --start 2020-01-01 --end 2026-10-10
```

Use the same command after an interruption. A checkout lock rejects a second concurrent full backfill. `--plan-only` fetches the schedule and audits existing data without ingesting games or summaries. `--manifest PATH` supports a separate manifest; the browser status endpoint reads the default `full-backfill.json`.

If the primary database has completed additional imports since the feature snapshot, stop the feature backfill and run `python -m app.reuse_primary --source PATH_TO_PRIMARY_DB`, then resume. The reuse command takes the same checkout lock and copies only in-scope final games that pass the metadata, score, raw-payload, and both-team stat-line audit. Newer feature records are preserved. Primary history and out-of-scope games are left for final publication reconciliation.

The process fetches Statcast summaries first, then batches known-player summaries, imports missing final games in monthly chunks (newest first), refreshes the schedule, retries remaining gaps, and enriches newly discovered players. Both API-level transient retries and bounded dataset/game retry passes are used. No worker sleeps longer than 60 seconds. Completed summaries and audited games are skipped on resume. A new ingestion run may be created for a retried chunk, while game records remain idempotent.

The long run continues in the background while the computer is awake. Closing the browser does not stop it. The process is not an installed Windows service; after reboot or process termination, rerun the command. Initial runtime depends on actual network throughput and may take hours. Raw box scores make the full database several gigabytes rather than a small summaries-only download.

## Progress and completeness

- The feature preview at http://127.0.0.1:5174 shows live backfill progress, with current monthly-job progress when games are being processed.
- Player pages have separate **MLB summaries** and **Statcast** tabs. Imported-game views remain explicitly scoped to local game coverage.
- `/api/backfill` exposes compact progress; `/api/players/{id}/source-stats` exposes independent source summaries.
- During the independent backfill, the primary API can read these two endpoints from that source using `MLB_ENRICHMENT_DATABASE_URL` (a read-only SQLite URI) and `MLB_BACKFILL_MANIFEST` (the absolute manifest path). Core game, roster, and ranking routes continue using the primary database. Remove these overrides after final data publication and refresh the API.
- `backend/full-backfill.json` records the expected game IDs, owned run IDs, successful Statcast exports, summary counts, errors and final coverage. `full-backfill.log` and `full-backfill-error.log` record process output. These runtime files and the database are excluded from Git.
- A final game is complete only when it has synced metadata matching the schedule's season, competition and scores, a raw box-score payload, and batting and pitching lines for both teams.
- In-progress or otherwise nonfinal games are reported separately, rather than falsely treated as missing final box scores. The schedule is refreshed at the end to pick up games that finish during ingestion.
- Status becomes `completed` only when all expected final games, all player/competition summary fetches and all 35 Savant exports pass. Unresolved gaps produce `needs_attention`, with exact missing game IDs and source errors retained. A completed run certifies this manifest's coverage checks, not every possible statistical correction or every historical provider field.

## Storage and review

Existing core game tables are unchanged. Three source tables plus a fetch ledger are added: `player_stat_summaries`, `savant_season_summaries`, `savant_season_details`, and `player_summary_fetches`. This additive change uses the application's existing schema-creation mechanism; deployed schema changes should still receive a migration baseline before release.

Summary rows retain source scope, competition, season, team/position metadata, full source statistics and retrieval time. Source responses/CSV exports are preserved in `source_payloads`. Separate ledger entries distinguish successful empty responses from missing fetches. MLB summary identities distinguish team stints from total rows, which must never be summed together. Savant pitch rows preserve player, team and pitch type. MLB IDs connect source data to players, while Savant rows for players not yet in the directory are retained for later matching.

Do not merge or replace the live main database with this snapshot as a file-copy operation. Review source changes first, reconcile main's ongoing explorer work, and use a planned data migration or point the reviewed application at the completed feature database after accounting for main's newer imports.

## Publish the completed data

`python -m app.publish_backfill --target PATH_TO_PRIMARY_DB` performs a read-only preflight against a SQLite backup snapshot of the source. It requires completed final-game coverage, every player's summary fetches, matching summary ledger counts, matching row counts for each seasonal Savant export, and an idle primary queue.

Add `--apply` after the preflight passes. Publication first creates a timestamped SQLite backup of the primary database, including WAL contents. It then merges within one write transaction, preserving primary-only games and newer primary records. Game batting and pitching lines are replaced together with their game record. Complete player summary fetches are replaced as units, including successful empty results. Backfill ingestion runs receive new primary IDs recorded in an idempotent publication ledger; copied primary jobs are not duplicated. Final-game coverage and foreign keys are checked before commit. A merged manifest is written beside the primary database with its remapped history IDs.

If newer primary game scores disagree with the manifest, publication rolls back and reports the conflict rather than overwriting the newer correction. Reconcile the expected schedule before retrying. Backups remain available for recovery. The primary application should be refreshed only after the code and data have both been published.

Validation: 29 backend tests passed; TypeScript and Vite production build passed. Tests cover summary grain, competition/sport filtering, idempotent replacement, batch omissions, successful empty summaries, coverage requirements, score corrections, pitch-type preservation, malformed CSV rejection, atomic manifest saves, Windows checkpoint-sharing retries, publication data preservation, job ID collisions, repeat publication, idle-queue enforcement, source row-count verification, transaction rollback, and reuse of audited primary games.

On Windows, `python -m app.watch_publication --pid BACKFILL_PID --target PATH_TO_PRIMARY_DB` observes an already verified backfill process and publishes after it exits successfully. It holds the Windows process handle to avoid PID reuse. It never interrupts or restarts ingestion. Unfinished or failed manifests prevent publication. It waits up to three hours for a newly queued primary import to finish. Its result is recorded in `full-backfill-publication.json`. Review the result and remove the primary API source overrides after publication. Tests verify that running, interrupted, and incomplete manifests cannot be published by the watcher.

The completion watcher requests temporary protection from idle system sleep while it observes ingestion and publishes data. It does not keep the display on or change saved power settings. The request ends when the watcher exits; explicit user sleep, shutdown, and reboot can still interrupt the run.

Game stat rows use (game_id, player_id, team_id) as their key, preserving rare suspended games where a player appears for both clubs. SQLite startup migrates the previous two-column key transactionally and creates a timestamped backup first. Publication also runs this migration before reconciliation.

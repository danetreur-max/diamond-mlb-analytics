# Player profiles feature review

Branch: `feature/player-profiles`, based on the initial `main` source baseline.

## Included

- Linkable player profile URLs, with season and competition in the URL and browser back/forward support.
- Overview, recent form (7/15/30 calendar days), home/away splits, imported season history, and game logs linking to box scores.
- Separate batting and pitching directory views, filtered by season and competition. Team columns show historical stints within that selection.
- Batting columns: G, PA, HR, AVG, OBP, SLG, OPS. Pitching columns: G, IP, ERA, WHIP, SO, K%.
- Rate calculations from combined counts; innings represented as integer outs internally. Null rates appear as a dash.
- Coverage and sample-size labels. Recent windows end at the latest imported player game, rather than the current date.
- Both batting and pitching appear for two-way players. Empty selections do not fabricate statistics.

The branch now also includes the full-backfill pipeline and separate MLB source-summary and Statcast tabs. See FULL_BACKFILL.md for scope, storage, progress and resume details. Imported season history remains separate from source career summaries. Baseball Reference and cross-provider ID mapping remain future phases.

## Review preview

Merged into `main`, preserving its standings, roster, ranking and header-help work. The completed backfill is published in the primary database. Review the main application at http://127.0.0.1:5173 (API port 8000). Main uses its own database and manifest; temporary feature-source overrides have been removed. The original isolated feature checkout remains available for reference.

## Validation

29 backend tests passed, including weighted rates, integer-out ERA, recent-window boundaries, season/competition separation, directory filtering, two-way players, traded-player team stints and empty coverage. TypeScript check and Vite production build passed. Browser review checked batting/pitching views, profile overview and splits, pitching game log and game links. No external source scraping is performed.

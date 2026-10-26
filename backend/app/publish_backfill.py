"""Reconcile an audited backfill into an idle primary SQLite database.

Run from backend: python -m app.publish_backfill --target PATH
The default is a read-only preflight; --apply performs the backed-up merge.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile

from sqlalchemy import create_engine
from .db import Base, migrate_game_keys
from . import enrichment  # Register additive summary tables.
from .full_backfill import save, SAVANT_EXPORTS, is_played_final


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path, readonly=False):
    db = sqlite3.connect(Path(path).resolve().as_uri() + ('?mode=ro' if readonly else ''), uri=True, timeout=60, factory=Connection)
    db.row_factory = sqlite3.Row
    return db


def coverage(db, expected):
    games = {str(r['id']): r for r in db.execute('SELECT * FROM games')}
    raw = {r[0].split('/')[2] for r in db.execute("SELECT endpoint FROM source_payloads WHERE endpoint LIKE '/game/%/boxscore'")}
    counts = []
    for table in ('player_game_batting', 'player_game_pitching'):
        counts.append(dict(db.execute(f'SELECT game_id,count(DISTINCT team_id) FROM {table} GROUP BY game_id')))
    missing = []
    for gid, item in expected.items():
        if not is_played_final(item):
            continue
        g = games.get(str(gid))
        if (g is None or not g['last_synced_at'] or str(gid) not in raw
                or any(c.get(int(gid), 0) < 2 for c in counts)
                or any(g[col] != item[key] for col, key in [('season', 'season'), ('game_type', 'type'), ('home_score', 'home_score'), ('away_score', 'away_score')])):
            missing.append(int(gid))
    return missing


def preflight(source, target, plan):
    if plan.get('status') != 'completed':
        raise ValueError('Backfill must be completed before publication')
    if coverage(source, plan['expected_games']):
        raise ValueError('Source does not pass the independent final-game coverage audit')
    players = {r[0] for r in source.execute('SELECT id FROM players')}
    fetched = {(r[0], r[1]) for r in source.execute('SELECT player_id,competition FROM player_summary_fetches')}
    if any((pid, competition) not in fetched for pid in players for competition in ('R', 'P')):
        raise ValueError('Source has players without completed summary fetches')
    if plan.get('savant_errors') or plan.get('summary_errors'):
        raise ValueError('Source enrichment is incomplete')
    for year in range(int(plan['start_date'][:4]), int(plan['end_date'][:4])+1):
        for dataset, role in SAVANT_EXPORTS:
            key = f'{dataset}:{year}:{role}'
            expected_count = plan.get('savant_completed', {}).get(key)
            if dataset == 'expected_statistics':
                count = source.execute('SELECT count(*) FROM savant_season_summaries WHERE season=? AND role=?', (year, role)).fetchone()[0]
            else:
                count = source.execute('SELECT count(*) FROM savant_season_details WHERE dataset=? AND season=? AND role=?', (dataset, year, role)).fetchone()[0]
            if not expected_count or count != expected_count:
                raise ValueError(f'Source Statcast export fails row-count verification: {key}')
    if source.execute('''SELECT 1 FROM player_summary_fetches f WHERE f.row_count !=
            (SELECT count(*) FROM player_stat_summaries s WHERE s.player_id=f.player_id AND s.competition=f.competition) LIMIT 1''').fetchone():
        raise ValueError('Source summary rows disagree with the fetch ledger')
    if target.execute("SELECT 1 FROM ingestion_runs WHERE status IN ('running','queued') LIMIT 1").fetchone():
        raise ValueError('Primary import queue must be idle before publication')


def put(db, table, row):
    cols = list(row.keys())
    names = ','.join('"'+c+'"' for c in cols)
    db.execute(f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({",".join("?" for _ in cols)})', tuple(row[c] for c in cols))


def merge(source, target, plan):
    """Caller holds a write transaction. Preserve newer primary records and extras."""
    for table in ('teams', 'players'):
        for row in source.execute(f'SELECT * FROM {table}'):
            old = target.execute(f'SELECT updated_at FROM {table} WHERE id=?', (row['id'],)).fetchone()
            if old is None or row['updated_at'] > old[0]:
                put(target, table, row)
    copied = 0
    for row in source.execute('SELECT * FROM games'):
        old = target.execute('SELECT last_synced_at FROM games WHERE id=?', (row['id'],)).fetchone()
        if old is not None and (old[0] or '') >= (row['last_synced_at'] or ''):
            continue
        put(target, 'games', row)
        for table in ('player_game_batting', 'player_game_pitching'):
            target.execute(f'DELETE FROM {table} WHERE game_id=?', (row['id'],))
            for stat in source.execute(f'SELECT * FROM {table} WHERE game_id=?', (row['id'],)):
                put(target, table, stat)
        copied += 1
    # Replace each complete fetch as a unit, including successful empty results.
    for row in source.execute('SELECT * FROM player_summary_fetches'):
        key = (row['player_id'], row['competition'])
        old = target.execute('SELECT fetched_at FROM player_summary_fetches WHERE player_id=? AND competition=?', key).fetchone()
        if old is not None and old[0] >= row['fetched_at']:
            continue
        target.execute('DELETE FROM player_stat_summaries WHERE player_id=? AND competition=?', key)
        for stat in source.execute('SELECT * FROM player_stat_summaries WHERE player_id=? AND competition=?', key):
            put(target, 'player_stat_summaries', stat)
        put(target, 'player_summary_fetches', row)
    for table, keys in [('source_payloads', ['endpoint']), ('savant_season_summaries', ['season', 'role', 'player_id']), ('savant_season_details', ['key'])]:
        where = ' AND '.join('"'+k+'"=?' for k in keys)
        for row in source.execute(f'SELECT * FROM {table}'):
            old = target.execute(f'SELECT fetched_at FROM {table} WHERE {where}', tuple(row[k] for k in keys)).fetchone()
            if old is None or row['fetched_at'] > old[0]:
                put(target, table, row)
    # A ledger makes retries idempotent and avoids colliding queue IDs.
    target.execute('CREATE TABLE IF NOT EXISTS backfill_publications (source_key TEXT, source_run INTEGER, target_run INTEGER, PRIMARY KEY(source_key,source_run))')
    mapping = {}
    for rid in plan['run_ids']:
        old = target.execute('SELECT target_run FROM backfill_publications WHERE source_key=? AND source_run=?', (plan['created_at'], rid)).fetchone()
        if old:
            mapping[rid] = old[0]
            continue
        row = source.execute('SELECT * FROM ingestion_runs WHERE id=?', (rid,)).fetchone()
        if row is None:
            raise ValueError(f'Missing owned ingestion run {rid}')
        values = dict(row)
        values['id'] = target.execute('SELECT coalesce(max(id),0)+1 FROM ingestion_runs').fetchone()[0]
        put(target, 'ingestion_runs', values)
        mapping[rid] = values['id']
        target.execute('INSERT INTO backfill_publications VALUES (?,?,?)', (plan['created_at'], rid, values['id']))
    missing = coverage(target, plan['expected_games'])
    if missing:
        raise ValueError(f'Merged database failed coverage for {len(missing)} games; transaction rolled back')
    if target.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Merged database has a foreign-key violation')
    return copied, mapping


def publish(source_path, target_path, manifest_path, apply=False):
    source_path, target_path, manifest_path = map(lambda p: Path(p).resolve(), (source_path, target_path, manifest_path))
    if source_path == target_path:
        raise ValueError('Source and target must be different databases')
    plan = json.loads(manifest_path.read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='mlb-publication-') as scratch:
        # SQLite backup captures live WAL contents; ordinary file copying does not.
        snapshot = Path(scratch) / 'source.db'
        with connect(source_path, True) as live, connect(snapshot) as copy:
            live.backup(copy)
        with connect(snapshot, True) as source, connect(target_path, True) as target:
            preflight(source, target, plan)
        if not apply:
            return {'preflight': 'passed', 'applied': False}
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_path = target_path.with_name(f'{target_path.stem}.before-backfill-{stamp}.db')
        with connect(target_path, True) as live, connect(backup_path) as backup:
            live.backup(backup)
        engine = create_engine('sqlite:///' + target_path.as_posix())
        Base.metadata.create_all(engine)
        migrate_game_keys(engine)
        engine.dispose()
        with connect(snapshot, True) as source, connect(target_path) as target:
            target.execute('BEGIN IMMEDIATE')
            preflight(source, target, plan)  # Queue may have changed since preflight.
            copied, mapping = merge(source, target, plan)
        published = dict(plan)
        published['run_ids'] = [mapping[rid] for rid in plan['run_ids']]
        if (published.get('active') or {}).get('run_id'):
            published['active'] = {**published['active'], 'run_id': mapping[published['active']['run_id']]}
        published['publication'] = {'source': str(source_path), 'backup': str(backup_path), 'games_updated': copied, 'at': stamp}
        save(target_path.parent / 'full-backfill.json', published)
        return {'applied': True, **published['publication']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('mlb.db'))
    parser.add_argument('--manifest', type=Path, default=Path('full-backfill.json'))
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(publish(args.source, args.target, args.manifest, args.apply), indent=2))

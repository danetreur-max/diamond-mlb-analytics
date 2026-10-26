"""Reuse audited final games from a newer primary database before resuming.

Stop the feature backfill first. Source is read-only and copied with SQLite backup.
"""
import argparse
import json
from pathlib import Path
import tempfile
import os
from .publish_backfill import connect, coverage, put


def reuse(source_path, target_path, manifest_path):
    with open(Path(target_path).resolve().parent / '.full-backfill.lock', 'a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Stop the feature backfill before reusing primary data')
        return _reuse(source_path, target_path, manifest_path)


def _reuse(source_path, target_path, manifest_path):
    if Path(source_path).resolve() == Path(target_path).resolve():
        raise ValueError('Source and target must differ')
    plan = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='mlb-reuse-') as scratch:
        snapshot = Path(scratch) / 'primary.db'
        with connect(source_path, True) as source, connect(snapshot) as copy:
            source.backup(copy)
        with connect(snapshot, True) as source, connect(target_path) as target:
            target.execute('BEGIN IMMEDIATE')
            # Only reuse games in this manifest that pass all final-game checks.
            present = {str(r[0]) for r in source.execute('SELECT id FROM games')}
            expected = {gid: item for gid, item in plan['expected_games'].items() if gid in present and item['state'] == 'Final'}
            missing = set(coverage(source, expected))
            eligible = {int(gid) for gid in expected} - missing
            for table in ('teams', 'players'):
                for row in source.execute(f'SELECT * FROM {table}'):
                    old = target.execute(f'SELECT updated_at FROM {table} WHERE id=?', (row['id'],)).fetchone()
                    if old is None or row['updated_at'] > old[0]:
                        put(target, table, row)
            copied = 0
            for gid in sorted(eligible):
                row = source.execute('SELECT * FROM games WHERE id=?', (gid,)).fetchone()
                old = target.execute('SELECT last_synced_at FROM games WHERE id=?', (gid,)).fetchone()
                if old is not None and (old[0] or '') >= row['last_synced_at']:
                    continue
                put(target, 'games', row)
                for table in ('player_game_batting', 'player_game_pitching'):
                    target.execute(f'DELETE FROM {table} WHERE game_id=?', (gid,))
                    for stat in source.execute(f'SELECT * FROM {table} WHERE game_id=?', (gid,)):
                        put(target, table, stat)
                raw = source.execute('SELECT * FROM source_payloads WHERE endpoint=?', (f'/game/{gid}/boxscore',)).fetchone()
                put(target, 'source_payloads', raw)
                copied += 1
            if target.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Foreign-key violation; reuse rolled back')
            return {'audited_primary_games':len(eligible), 'games_copied':copied, 'remaining_final_games':len(coverage(target, plan['expected_games']))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, default=Path('mlb.db'))
    parser.add_argument('--manifest', type=Path, default=Path('full-backfill.json'))
    args = parser.parse_args()
    print(json.dumps(reuse(args.source, args.target, args.manifest), indent=2))

"""Resumable, audited MLB backfill for this checkout's database only.

python -m app.full_backfill --start 2020-01-01 --end 2026-10-10
Re-run the same command to resume. Use --plan-only to inspect without ingestion.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
import time
import httpx
from sqlalchemy import select, func
from .db import Session, Game, Player, Batting, Pitching, Payload, Run, init_db, now
from .enrichment import SummaryFetch, PlayerSummary, SavantSummary, ingest_player_summary, ingest_player_batch, ingest_savant
from .ingest import fetch, run_import

TYPES = {'R', 'F', 'D', 'L', 'W'}
SAVANT_EXPORTS = [('expected_statistics', 'batter'), ('expected_statistics', 'pitcher'), ('statcast', 'batter'), ('statcast', 'pitcher'), ('pitch-arsenal-stats', 'pitcher')]


def save(path, plan):
    plan['updated_at'] = now()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    # Windows readers can briefly deny replacement while the preview reads JSON.
    for attempt in range(20):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.05 * (attempt + 1))


def schedule(client, start, end):
    expected = {}
    for year in range(start.year, end.year + 1):
        payload = fetch(client, '/schedule', {'sportId': 1, 'startDate': str(max(start, date(year, 1, 1))), 'endDate': str(min(end, date(year, 12, 31)))})
        for day in payload.get('dates', []):
            for game in day['games']:
                if game['gameType'] in TYPES:
                    expected[str(game['gamePk'])] = {
                        'season': int(game['season']), 'date': game['officialDate'], 'schedule_date': day['date'],
                        'type': game['gameType'], 'state': game['status']['abstractGameState'],
                        'status': game['status']['detailedState'],
                        'home_score': game['teams']['home'].get('score'), 'away_score': game['teams']['away'].get('score')}
        time.sleep(.2)
    if not expected:
        raise ValueError('No games returned for requested scope')
    return expected


def is_played_final(item):
    # MLB also assigns abstract Final to cancelled, unplayed fixtures.
    return item['state'] == 'Final' and item.get('status') not in {'Cancelled', 'Canceled'}


def audit(expected):
    with Session() as session:
        games = {str(g.id): g for g in session.scalars(select(Game))}
        raw_ids = {p.rsplit('/', 2)[-2] for p in session.scalars(select(Payload.endpoint).where(Payload.endpoint.like('/game/%/boxscore')))}
        batting = dict(session.execute(select(Batting.game_id, func.count(func.distinct(Batting.team_id))).group_by(Batting.game_id)).all())
        pitching = dict(session.execute(select(Pitching.game_id, func.count(func.distinct(Pitching.team_id))).group_by(Pitching.game_id)).all())
        missing = []
        final = 0
        for gid, item in expected.items():
            if not is_played_final(item):
                continue
            final += 1
            game = games.get(gid)
            if (not game or not game.last_synced_at or gid not in raw_ids or batting.get(int(gid), 0) < 2 or pitching.get(int(gid), 0) < 2
                or game.season != item['season'] or game.game_type != item['type']
                or game.home_score != item['home_score'] or game.away_score != item['away_score']):
                missing.append(int(gid))
    return {'final_expected': final, 'final_complete': final-len(missing), 'missing_game_ids': missing,
            'nonfinal_by_status': dict(Counter(g['status'] for g in expected.values() if not is_played_final(g)))}


def ingest_missing(plan, path):
    for attempt in range(1, 4):
        coverage = audit(plan['expected_games'])
        plan['coverage'] = coverage
        save(path, plan)
        if not coverage['missing_game_ids']:
            return
        months = defaultdict(list)
        for gid in coverage['missing_game_ids']:
            months[plan['expected_games'][str(gid)]['schedule_date'][:7]].append(gid)
        for month, ids in sorted(months.items(), reverse=True):
            import calendar
            year, number = map(int, month.split('-'))
            with Session.begin() as session:
                run = Run(start_date=f'{month}-01', end_date=str(min(date.fromisoformat(plan['end_date']), date(year, number, calendar.monthrange(year, number)[1]))))
                session.add(run)
                session.flush()
                rid = run.id
            plan['active'] = {'phase': 'games', 'month': month, 'run_id': rid, 'games': len(ids), 'attempt': attempt}
            plan['run_ids'].append(rid)
            save(path, plan)
            print(json.dumps(plan['active']), flush=True)
            run_import(rid, game_ids=set(ids), allowed_types=TYPES)
            plan['coverage'] = audit(plan['expected_games'])
            save(path, plan)


def enrich(plan, path, client):
    plan['summary_errors'] = []
    with Session() as session:
        ids = list(session.scalars(select(Player.id).order_by(Player.id)))
    plan['summary_players_expected'] = len(ids)
    for attempt in range(3):
        with Session() as session:
            done = {(r.player_id, r.competition) for r in session.scalars(select(SummaryFetch))}
        pending = [(pid, competition) for pid in ids for competition in ['R', 'P'] if (pid, competition) not in done]
        if not pending:
            break
        for competition in ['R', 'P']:
            players = [pid for pid, c in pending if c == competition]
            size = 25 if attempt < 2 else 1
            for index in range(0, len(players), size):
                batch = players[index:index+size]
                plan['active'] = {'phase': 'summaries', 'player_ids': batch, 'competition': competition,
                                  'remaining_this_pass': len(players)-index, 'attempt': attempt+1}
                save(path, plan)
                print(json.dumps(plan['active']), flush=True)
                try:
                    if size == 1:
                        ingest_player_summary(client, batch[0], competition)
                    else:
                        ingest_player_batch(client, batch, competition)
                except Exception as exc:
                    plan['summary_errors'].append({'player_ids': batch, 'competition': competition, 'attempt': attempt+1, 'error': str(exc)[:300]})
                time.sleep(.2)
    with Session() as session:
        done = {(r.player_id, r.competition) for r in session.scalars(select(SummaryFetch))}
        plan['summaries_complete'] = sum((pid, c) in done for pid in ids for c in ['R', 'P'])
        plan['summary_requests_expected'] = len(ids)*2
        plan['summary_rows'] = session.scalar(select(func.count()).select_from(PlayerSummary))
    save(path, plan)


def savant(plan, path, client):
    plan['savant_errors'] = []
    for year in range(date.fromisoformat(plan['start_date']).year, date.fromisoformat(plan['end_date']).year+1):
        for dataset, role in SAVANT_EXPORTS:
            key = f'{dataset}:{year}:{role}'
            if key in plan['savant_completed']:
                continue
            for attempt in range(3):
                plan['active'] = {'phase': 'statcast_summaries', 'dataset': dataset, 'season': year, 'role': role, 'attempt': attempt+1}
                save(path, plan)
                try:
                    count = ingest_savant(client, year, role, dataset)
                    plan['savant_completed'][key] = count
                    break
                except Exception as exc:
                    if attempt == 2:
                        plan['savant_errors'].append({'dataset': dataset, 'season': year, 'role': role, 'error': str(exc)[:300]})
                    time.sleep(2**attempt)
            save(path, plan)


def run(args):
    init_db()
    path = args.manifest.resolve()
    with httpx.Client(timeout=45, headers={'User-Agent': 'MLBExplorer/0.2 (personal statistics backfill)'}, follow_redirects=True) as client:
        if path.exists():
            plan = json.loads(path.read_text(encoding='utf-8'))
            if plan['start_date'] != str(args.start) or plan['end_date'] != str(args.end):
                raise ValueError('Existing manifest uses a different scope; choose another manifest')
        else:
            plan = {'schema_version': 1, 'created_at': now(), 'start_date': str(args.start), 'end_date': str(args.end),
                    'status': 'planned', 'game_types': sorted(TYPES), 'run_ids': [], 'savant_completed': {},
                    'expected_games': schedule(client, args.start, args.end)}
        plan['coverage'] = audit(plan['expected_games'])
        save(path, plan)
        print(json.dumps({'manifest': str(path), 'coverage': {k: v for k, v in plan['coverage'].items() if k != 'missing_game_ids'}, 'games_to_import': len(plan['coverage']['missing_game_ids'])}), flush=True)
        if args.plan_only:
            return
        # Only recover jobs owned by this manifest. Never consume the copied main queue.
        with Session.begin() as session:
            for rid in plan['run_ids']:
                job = session.get(Run, rid)
                if job and job.status in ('running', 'queued'):
                    job.status = 'failed'
                    job.finished_at = now()
                    job.errors = [{'error': 'Backfill resumed; remaining games will be audited and retried.'}]
        plan['status'] = 'running'
        if not plan.get('source_snapshot_queue_disabled'):
            with Session.begin() as session:
                for job in session.scalars(select(Run).where(Run.status.in_(['running', 'queued']))):
                    if job.id not in plan['run_ids']:
                        job.status = 'snapshot'
                        job.finished_at = now()
                        job.errors = [{'error': 'Copied main-checkout queue; intentionally not executed in the feature backfill.'}]
            plan['source_snapshot_queue_disabled'] = True
        save(path, plan)
        # Fast enrichment first makes the player page useful while game imports continue.
        savant(plan, path, client)
        enrich(plan, path, client)
        ingest_missing(plan, path)
        plan['expected_games'] = schedule(client, args.start, args.end)
        ingest_missing(plan, path)
        # Include players discovered in historical boxes and the final schedule refresh.
        enrich(plan, path, client)
        plan['coverage'] = audit(plan['expected_games'])
        all_savant = (args.end.year-args.start.year+1)*len(SAVANT_EXPORTS)
        complete = not plan['coverage']['missing_game_ids'] and plan['summaries_complete'] == plan['summary_requests_expected'] and len(plan['savant_completed']) == all_savant
        plan['status'] = 'completed' if complete else 'needs_attention'
        plan['active'] = None
        plan['finished_at'] = now()
        save(path, plan)
        print(json.dumps({'status': plan['status'], 'coverage': plan['coverage'], 'summary_rows': plan['summary_rows'], 'savant_completed': plan['savant_completed']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--manifest', type=Path, default=Path('full-backfill.json'))
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if args.start > args.end or args.end > date.today() or args.start.year < 2020:
        parser.error('Use an ordered range starting in 2020 or later and ending no later than today')
    lock = open('.full-backfill.lock', 'a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        import os
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('A full backfill is already running in this checkout')
    try:
        run(args)
    except Exception as exc:
        if args.manifest.exists():
            plan = json.loads(args.manifest.read_text(encoding='utf-8'))
            plan['status'] = 'interrupted'
            plan['last_error'] = str(exc)[:500]
            save(args.manifest, plan)
        raise


if __name__ == '__main__':
    main()

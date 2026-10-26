"""Queue a bounded historical backfill as durable monthly import jobs.

python -m app.backfill --start 2025-01-01 --end 2026-10-10 --manifest PATH
The manifest lets a caller audit coverage and inspect exactly these jobs.
"""
import argparse
import calendar
import json
from datetime import date, timedelta
from pathlib import Path
import httpx
from .db import Session, Run, init_db, now
from .ingest import fetch

def chunks(start, end):
    while start <= end:
        stop = min(end, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]))
        yield start, stop
        start = stop + timedelta(days=1)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, type=date.fromisoformat)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    if args.start > args.end or args.end > date.today():
        parser.error('Dates must be ordered and cannot end in the future.')
    if args.manifest.exists():
        parser.error('Manifest already exists; inspect its run IDs instead of queueing a duplicate backfill.')
    expected = {}
    with httpx.Client(timeout=45) as client:
        for year in range(args.start.year, args.end.year + 1):
            data = fetch(client, '/schedule', {'sportId': 1, 'startDate': str(max(args.start, date(year, 1, 1))), 'endDate': str(min(args.end, date(year, 12, 31)))})
            for day in data.get('dates', []):
                for game in day['games']:
                    expected[str(game['gamePk'])] = {'season': int(game['season']), 'date': game['officialDate'], 'type': game['gameType'], 'status': game['status']['detailedState']}
    init_db()
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    plan = {'created_at': now(), 'start_date': str(args.start), 'end_date': str(args.end), 'expected_games': expected, 'runs': []}
    with Session.begin() as session:
        for start, end in chunks(args.start, args.end):
            run = Run(start_date=str(start), end_date=str(end))
            session.add(run)
            session.flush()
            plan['runs'].append({'id': run.id, 'start_date': str(start), 'end_date': str(end)})
        # Write before commit: a manifest-write failure rolls the queue back.
        args.manifest.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    print(json.dumps({'manifest': str(args.manifest.resolve()), 'jobs_queued': len(plan['runs']), 'unique_expected_games': len(expected), 'run_ids': [r['id'] for r in plan['runs']]}), flush=True)

if __name__ == '__main__':
    main()

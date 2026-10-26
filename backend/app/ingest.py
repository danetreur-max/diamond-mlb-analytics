"""Independent durable importer. One game is committed atomically at a time."""
import sys
import time
from datetime import date, timedelta
import httpx
from sqlalchemy import delete
from .db import Session, Team, Player, Game, Batting, Pitching, Run, Payload, now, upsert

ROOT = 'https://statsapi.mlb.com/api/v1'

def fetch(client, path, params=None):
    for attempt in range(5):
        try:
            response = client.get(ROOT + path, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    response.raise_for_status()
                delay = response.headers.get('Retry-After', '')
                time.sleep(min(float(delay), 60) if delay.isdigit() else 2 ** attempt)
                continue
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, ValueError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)

def outs(innings):
    whole, _, remainder = str(innings).partition('.')
    if remainder not in ('', '0', '1', '2'):
        raise ValueError(f'Invalid baseball innings: {innings}')
    return int(whole) * 3 + int(remainder or 0)

def store_box(session, game, box):
    # Replace both sets in this transaction, including removed/corrected lines.
    for cls in (Batting, Pitching):
        session.execute(delete(cls).where(cls.game_id == game.id))
    for side in ('home', 'away'):
        team = box['teams'][side]
        team_id = team['team']['id']
        for entry in team.get('players', {}).values():
            person = entry['person']
            pid = person['id']
            existing = session.get(Player, pid)
            upsert(session, Player, pid, dict(id=pid, full_name=person['fullName'], primary_position=person.get('primaryPosition', entry.get('position', {})).get('abbreviation', ''), bat_side=person.get('batSide', entry.get('batSide', {})).get('code', existing.bat_side if existing else ''), throw_hand=person.get('pitchHand', entry.get('pitchHand', {})).get('code', existing.throw_hand if existing else ''), updated_at=now()))
            for group, cls in [('batting', Batting), ('pitching', Pitching)]:
                stats = entry.get('stats', {}).get(group, {})
                if not stats:
                    continue
                # Persist counting stats only; derive rates from aggregated counts.
                counts = {k: v for k, v in stats.items() if isinstance(v, int) and not isinstance(v, bool)}
                values = dict(game_id=game.id, player_id=pid, team_id=team_id, stats=counts, updated_at=now())
                if cls is Pitching:
                    values['outs_recorded'] = outs(stats.get('inningsPitched', '0.0'))
                session.add(cls(**values))
    endpoint = f'/game/{game.id}/boxscore'
    upsert(session, Payload, endpoint, dict(endpoint=endpoint, fetched_at=now(), payload=box))
    game.last_synced_at = now()

def run_import(run_id, game_ids=None, allowed_types=None):
    with Session() as session:
        run = session.get(Run, run_id)
        if not run or run.status != 'queued':
            return
        run.status = 'running'
        session.commit()
    errors = []
    try:
        with httpx.Client(timeout=30, headers={'User-Agent': 'MLBExplorer/0.1 (personal stats explorer)'}) as client:
            with Session() as session:
                for team in fetch(client, '/teams', {'sportId': 1})['teams']:
                    upsert(session, Team, team['id'], dict(id=team['id'], name=team['name'], abbreviation=team.get('abbreviation', ''), league=team.get('league', {}).get('name', ''), division=team.get('division', {}).get('name', ''), updated_at=now()))
                session.commit()
                run = session.get(Run, run_id)
                start, end = date.fromisoformat(run.start_date), date.fromisoformat(run.end_date)
            games = {}
            cursor = start
            while cursor <= end:
                stop = min(cursor + timedelta(days=6), end)
                schedule = fetch(client, '/schedule', {'sportId': 1, 'startDate': str(cursor), 'endDate': str(stop)})
                for day in schedule.get('dates', []):
                    for game in day['games']:
                        if (game_ids is None or game['gamePk'] in game_ids) and (allowed_types is None or game['gameType'] in allowed_types):
                            games[game['gamePk']] = game
                with Session() as session:
                    run = session.get(Run, run_id)
                    run.heartbeat_at = now()
                    session.commit()
                cursor = stop + timedelta(days=1)
            with Session() as session:
                session.get(Run, run_id).total_games = len(games)
                session.commit()
            profiles = {}
            for raw in games.values():
                gid = raw['gamePk']
                try:
                    # Spring/exhibition games can include clubs outside the MLB directory.
                    for side in ('home', 'away'):
                        opponent = raw['teams'][side]['team']
                        with Session() as session:
                            known = session.get(Team, opponent['id']) is not None
                        if not known:
                            team = fetch(client, f"/teams/{opponent['id']}")['teams'][0]
                            with Session.begin() as session:
                                upsert(session, Team, team['id'], dict(id=team['id'], name=team['name'], abbreviation=team.get('abbreviation', ''), league=team.get('league', {}).get('name', ''), division=team.get('division', {}).get('name', ''), updated_at=now()))
                    state = raw['status']['abstractGameState']
                    box = fetch(client, f'/game/{gid}/boxscore') if state in ('Final', 'Live') else None
                    if box:
                        entries = [p for side in ('home', 'away') for p in box['teams'][side].get('players', {}).values()]
                        missing = sorted({p['person']['id'] for p in entries} - profiles.keys())
                        for index in range(0, len(missing), 50):
                            people = fetch(client, '/people', {'personIds': ','.join(map(str, missing[index:index+50]))})
                            profiles.update({p['id']: p for p in people.get('people', [])})
                        for entry in entries:
                            entry['person'] = {**entry['person'], **profiles.get(entry['person']['id'], {})}
                    with Session.begin() as session:
                        game = upsert(session, Game, gid, dict(id=gid, season=int(raw['season']), game_type=raw['gameType'], official_date=raw['officialDate'], scheduled_at=raw['gameDate'], status=raw['status']['detailedState'], home_team_id=raw['teams']['home']['team']['id'], away_team_id=raw['teams']['away']['team']['id'], home_score=raw['teams']['home'].get('score'), away_score=raw['teams']['away'].get('score')))
                        session.flush()
                        if box:
                            store_box(session, game, box)
                        run = session.get(Run, run_id)
                        run.processed_games += 1
                        run.heartbeat_at = now()
                except Exception as exc:
                    errors.append({'game_id': gid, 'error': str(exc)[:500]})
                    with Session.begin() as session:
                        run = session.get(Run, run_id)
                        run.errors = list(errors)
                        run.heartbeat_at = now()
                time.sleep(0.2)
    except Exception as exc:
        errors.append({'error': str(exc)[:500]})
    finally:
        with Session.begin() as session:
            run = session.get(Run, run_id)
            run.errors = errors
            run.status = ('partial' if run.processed_games else 'failed') if errors else 'completed'
            run.finished_at = now()
            run.heartbeat_at = now()

if __name__ == '__main__':
    run_import(int(sys.argv[1]))

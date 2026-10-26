"""Explorer views backed by cached MLB snapshots and explicitly scoped local logs."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Literal
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from .db import Session, Payload, Player, Game, Batting, Pitching, now, upsert
from .ingest import fetch, outs

router = APIRouter(prefix='/api/explore', tags=['Explorer'])
_locks = defaultdict(Lock)
TODAY = lambda: datetime.now(timezone.utc).astimezone().date()

def snapshot(path, params=None, ttl=1800):
    key = 'explorer:' + path + '?' + urlencode(sorted((params or {}).items()))
    with _locks[key]:
        with Session() as session:
            existing = session.get(Payload, key)
            if existing and (datetime.now(timezone.utc) - datetime.fromisoformat(existing.fetched_at)).total_seconds() < ttl:
                return existing.payload, existing.fetched_at, False
            stale = (existing.payload, existing.fetched_at, True) if existing else None
        try:
            with httpx.Client(timeout=35) as client:
                value = fetch(client, path, params)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            if stale:
                return stale
            raise HTTPException(502, 'MLB data is temporarily unavailable. Please retry.') from exc
        fetched = now()
        with Session.begin() as session:
            upsert(session, Payload, key, dict(endpoint=key, payload=value, fetched_at=fetched))
        return value, fetched, False

def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def ratio(a, b, scale=1):
    return a * scale / b if b else None

def metrics(counts, discipline):
    result = dict(counts)
    if discipline == 'batting':
        ab, hits = counts.get('atBats', 0), counts.get('hits', 0)
        walks, hbp, sf = counts.get('baseOnBalls', 0), counts.get('hitByPitch', 0), counts.get('sacFlies', 0)
        bases = counts.get('totalBases', hits + counts.get('doubles', 0) + 2 * counts.get('triples', 0) + 3 * counts.get('homeRuns', 0))
        result.update(avg=ratio(hits, ab), obp=ratio(hits + walks + hbp, ab + walks + hbp + sf), slg=ratio(bases, ab))
        result['ops'] = result['obp'] + result['slg'] if result['obp'] is not None and result['slg'] is not None else None
    else:
        recorded = counts.get('outs_recorded', counts.get('outs', 0))
        result.update(outs_recorded=recorded, inningsPitched=f'{recorded // 3}.{recorded % 3}', era=ratio(counts.get('earnedRuns', 0), recorded, 27), whip=ratio(counts.get('hits', 0) + counts.get('baseOnBalls', 0), recorded, 3))
    return advanced_stats(result, discipline)

def advanced_stats(stats, discipline):
    """Derived rates from the same scoped totals; missing inputs stay missing."""
    result = dict(stats)
    def rate(key, numerator, denominator, scale=1):
        result[key] = numerator * scale / denominator if numerator is not None and denominator else None
    def difference(*keys):
        values = [number(result.get(k)) for k in keys]
        return values[0] - sum(values[1:]) if all(v is not None for v in values) else None
    denominator = number(result.get('plateAppearances' if discipline == 'batting' else 'battersFaced'))
    rate('kPct', number(result.get('strikeOuts')), denominator, 100)
    rate('bbPct', number(result.get('baseOnBalls')), denominator, 100)
    result['kMinusBbPct'] = difference('kPct', 'bbPct')
    if discipline == 'batting':
        result['iso'] = difference('slg', 'avg')
        values = [number(result.get(k)) for k in ('atBats','strikeOuts','homeRuns','sacFlies')]
        bip = values[0]-values[1]-values[2]+values[3] if all(v is not None for v in values) else None
        rate('babip', difference('hits','homeRuns'), bip)
    else:
        recorded = number(result.get('outs_recorded'))
        for key, source in [('k9','strikeOuts'),('bb9','baseOnBalls'),('hr9','homeRuns')]:
            rate(key, number(result.get(source)), recorded, 27)
        rate('kbb', number(result.get('strikeOuts')), number(result.get('baseOnBalls')))
    return result

def normalize_stats(stats, discipline):
    result = dict(stats)
    for key in ('avg', 'obp', 'slg', 'ops', 'era', 'whip'):
        if key in result:
            result[key] = number(result[key])
    if discipline == 'pitching':
        result['outs_recorded'] = stats.get('outs', outs(stats.get('inningsPitched', '0.0')))
    return advanced_stats(result, discipline)

def aggregate(rows, discipline):
    totals = defaultdict(int)
    for row in rows:
        for key, value in row['stats'].items():
            if isinstance(value, int) and not isinstance(value, bool) and key not in ('age', 'rank'):
                totals[key] += value
        if discipline == 'pitching':
            totals['outs_recorded'] += row.get('outs_recorded', row['stats'].get('outs', 0))
    totals['gamesPlayed'] = len(rows)
    return metrics(dict(totals), discipline)

def season_splits(season, discipline, game_type='R', qualified=False):
    params = {'stats': 'season', 'group': 'hitting' if discipline == 'batting' else 'pitching', 'sportIds': 1, 'season': season, 'gameType': game_type, 'playerPool': 'QUALIFIED' if qualified else 'ALL', 'limit': 1000}
    splits, offset, stamp, stale = [], 0, None, False
    while True:
        data, stamp, cached_stale = snapshot('/stats', {**params, 'offset': offset})
        stale = stale or cached_stale
        groups = data.get('stats', [])
        group = groups[0] if groups else {}
        page = group.get('splits', [])
        splits.extend(page)
        offset += len(page)
        if not page or offset >= group.get('totalSplits', offset):
            break
    return splits, stamp, stale

def schedule(season, team_id=None):
    params = {'sportId': 1, 'startDate': f'{season}-01-01', 'endDate': str(min(TODAY(), date(season, 12, 31)))}
    if team_id:
        params['teamId'] = team_id
    data, stamp, stale = snapshot('/schedule', params)
    games = {}
    for day in data.get('dates', []):
        for raw in day['games']:
            games[raw['gamePk']] = dict(id=raw['gamePk'], season=int(raw['season']), game_type=raw['gameType'], official_date=raw['officialDate'], scheduled_at=raw['gameDate'], status=raw['status']['detailedState'], abstract_state=raw['status']['abstractGameState'], home_team_id=raw['teams']['home']['team']['id'], away_team_id=raw['teams']['away']['team']['id'], home_name=raw['teams']['home']['team']['name'], away_name=raw['teams']['away']['team']['name'], home_score=raw['teams']['home'].get('score'), away_score=raw['teams']['away'].get('score'))
    return list(games.values()), stamp, stale

def coverage(season, game_type='R', team_id=None):
    games, stamp, stale = schedule(season, team_id)
    expected = {g['id'] for g in games if g['game_type'] == game_type and g['abstract_state'] in ('Final', 'Live') and g['home_score'] is not None and g['away_score'] is not None}
    with Session() as session:
        imported = set(session.scalars(select(Game.id).where(Game.season == season, Game.game_type == game_type, Game.last_synced_at.is_not(None))))
    loaded = len(expected & imported)
    return dict(expected_games=len(expected), imported_games=loaded, complete=bool(expected) and loaded == len(expected), source='MLB schedule compared with imported box scores', fetched_at=stamp, stale=stale)

@router.get('/coverage')
def get_coverage(season: int = Query(2026, ge=1900, le=2100), game_type: str = 'R'):
    return coverage(season, game_type)

@router.get('/standings')
def standings(season: int = Query(2026, ge=1900, le=2100)):
    data, stamp, stale = snapshot('/standings', {'leagueId': '103,104', 'season': season, 'standingsTypes': 'regularSeason'})
    teams_data, _, _ = snapshot('/teams', {'sportId': 1, 'season': season}, ttl=86400)
    directory = {t['id']: t for t in teams_data.get('teams', [])}
    result = []
    for division in data.get('records', []):
        for record in division.get('teamRecords', []):
            team = directory.get(record['team']['id'], record['team'])
            recent = next((r for r in record.get('records', {}).get('splitRecords', []) if r['type'] == 'lastTen'), {})
            result.append(dict(id=team['id'], name=team['name'], abbreviation=team.get('abbreviation', ''), league=team.get('league', {}).get('name', ''), division=team.get('division', {}).get('name', ''), wins=record.get('wins'), losses=record.get('losses'), pct=number(record.get('winningPercentage')), division_rank=number(record.get('divisionRank')), league_rank=number(record.get('leagueRank')), mlb_rank=number(record.get('sportRank')), games_back=record.get('gamesBack'), league_games_back=record.get('leagueGamesBack'), mlb_games_back=record.get('sportGamesBack'), run_differential=record.get('runDifferential'), runs_scored=record.get('runsScored'), runs_allowed=record.get('runsAllowed'), last_ten=f"{recent.get('wins', '—')}-{recent.get('losses', '—')}", streak=record.get('streak', {}).get('streakCode'), games_played=record.get('gamesPlayed'), source_updated_at=record.get('lastUpdated')))
    return dict(items=result, season=season, source='MLB official regular-season standings', fetched_at=stamp, stale=stale)

@router.get('/teams/{team_id}')
def team_detail(team_id: int, season: int = Query(2026, ge=1900, le=2100), game_type: str = 'R'):
    data, stamp, stale = snapshot(f'/teams/{team_id}', {'season': season}, ttl=86400)
    if not data.get('teams'):
        raise HTTPException(404, 'Team not found')
    team = data['teams'][0]
    stats_data, stats_stamp, stats_stale = snapshot(f'/teams/{team_id}/stats', {'stats': 'season', 'group': 'hitting,pitching', 'season': season, 'gameType': game_type})
    team_stats = {}
    for group in stats_data.get('stats', []):
        discipline = 'batting' if group['group']['displayName'] == 'hitting' else 'pitching'
        values = group.get('splits', [])
        team_stats[discipline] = normalize_stats(values[0]['stat'], discipline) if values else {}
    table = standings(season)['items']
    games, game_stamp, game_stale = schedule(season, team_id)
    games = sorted((g for g in games if g['game_type'] == game_type), key=lambda g: (g['official_date'], g['id']), reverse=True)
    return dict(id=team_id, name=team['name'], abbreviation=team.get('abbreviation', ''), league=team.get('league', {}).get('name', ''), division=team.get('division', {}).get('name', ''), venue=team.get('venue', {}).get('name'), standing=next((t for t in table if t['id'] == team_id), None), stats=team_stats, games=games, coverage=coverage(season, game_type, team_id), season=season, fetched_at=stats_stamp, stale=stale or stats_stale or game_stale)

@router.get('/teams/{team_id}/roster')
def roster(team_id: int, season: int = Query(2026, ge=1900, le=2100), roster_type: Literal['active', '40Man'] = 'active', as_of: date | None = None):
    as_of = as_of or min(TODAY(), date(season, 12, 31))
    if as_of > TODAY():
        raise HTTPException(422, 'Roster date cannot be in the future.')
    data, stamp, stale = snapshot(f'/teams/{team_id}/roster', {'rosterType': roster_type, 'season': as_of.year, 'date': str(as_of), 'hydrate': 'person'})
    batting, _, _ = season_splits(season, 'batting')
    pitching, _, _ = season_splits(season, 'pitching')
    stats = {'batting': {s['player']['id']: normalize_stats(s['stat'], 'batting') for s in batting}, 'pitching': {s['player']['id']: normalize_stats(s['stat'], 'pitching') for s in pitching}}
    items = []
    for entry in data.get('roster', []):
        person = entry['person']
        birth = date.fromisoformat(person['birthDate']) if person.get('birthDate') else None
        age = as_of.year - birth.year - ((as_of.month, as_of.day) < (birth.month, birth.day)) if birth else None
        items.append(dict(id=person['id'], full_name=person['fullName'], position=entry.get('position', {}).get('abbreviation', ''), jersey_number=entry.get('jerseyNumber'), status=entry.get('status', {}).get('description'), age=age, bat_side=person.get('batSide', {}).get('code'), throw_hand=person.get('pitchHand', {}).get('code'), batting=stats['batting'].get(person['id'], {}), pitching=stats['pitching'].get(person['id'], {})))
    return dict(items=sorted(items, key=lambda p: p['full_name']), as_of=str(as_of), season=season, roster_type=roster_type, source='MLB roster snapshot; displayed stats are full-season totals across all clubs', fetched_at=stamp, stale=stale)

@router.get('/players')
def ranked_players(season: int = Query(2026, ge=1900, le=2100), discipline: Literal['batting', 'pitching'] = 'batting', game_type: str = 'R', q: str = '', team_id: int | None = None, position: str = '', qualified: bool = False, sort: str = 'ops', direction: Literal['asc', 'desc'] = 'desc', offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), min_pa: int = 0, min_ip: float = 0, min_age: int | None = None, max_age: int | None = None, pitcher_role: Literal['all','starter','reliever'] = 'all'):
    from .workspace import sample_matches
    if min_pa < 0 or min_ip < 0 or (min_age is not None and max_age is not None and min_age > max_age):
        raise HTTPException(422, 'Minimum samples must be nonnegative and age ranges must be ordered.')
    if qualified and game_type != 'R':
        raise HTTPException(422, 'MLB qualification is available for the regular season only.')
    allowed = {'iso','babip','kPct','bbPct','kMinusBbPct','k9','bb9','hr9','kbb','full_name', 'team_name', 'position', 'age', 'gamesPlayed', 'plateAppearances', 'atBats', 'hits', 'homeRuns', 'rbi', 'stolenBases', 'avg', 'obp', 'slg', 'ops', 'gamesStarted', 'outs_recorded', 'wins', 'losses', 'era', 'whip', 'strikeOuts', 'baseOnBalls', 'saves'}
    if sort not in allowed:
        raise HTTPException(422, 'Unsupported sorting column.')
    splits, stamp, stale = season_splits(season, discipline, game_type, qualified)
    with Session() as session:
        local = {p.id: p for p in session.scalars(select(Player))}
    rows = []
    for split in splits:
        person, team = split['player'], split.get('team', {})
        pid = person['id']
        row = dict(id=pid, full_name=person['fullName'], team_id=team.get('id'), team_name=team.get('name', 'Multiple teams'), position=split.get('position', {}).get('abbreviation') or (local[pid].primary_position if pid in local else ''), num_teams=split.get('numTeams', 1), **normalize_stats(split['stat'], discipline))
        if (team_id is None or row['team_id'] == team_id) and (not position or row['position'] == position) and sample_matches(row, min_pa, min_ip, min_age, max_age, pitcher_role):
            rows.append(row)
    valid, absent = [r for r in rows if r.get(sort) is not None], [r for r in rows if r.get(sort) is None]
    valid.sort(key=lambda r: (r[sort].casefold() if isinstance(r[sort], str) else r[sort]), reverse=direction == 'desc')
    previous, rank = object(), 0
    for index, row in enumerate(valid):
        if row[sort] != previous:
            rank = index + 1
        row['rank'], previous = rank, row[sort]
    for row in absent:
        row['rank'] = None
    rows = [r for r in valid + absent if q.casefold() in r['full_name'].casefold()]
    return dict(items=rows[offset:offset+limit], total=len(rows), season=season, discipline=discipline, source='MLB official season totals', fetched_at=stamp, stale=stale, qualified=qualified, note='Season totals include all clubs. Team filter uses the latest season team listed by MLB. Rank is within the selected team/position/qualification pool before name search.')

def local_logs(player_id, season, game_type):
    result = {}
    with Session() as session:
        for discipline, cls in [('batting', Batting), ('pitching', Pitching)]:
            rows = []
            for line, game in session.execute(select(cls, Game).join(Game, cls.game_id == Game.id).where(cls.player_id == player_id, Game.season == season, Game.game_type == game_type).order_by(Game.official_date, Game.id)):
                item = dict(game_id=game.id, player_id=player_id, team_id=line.team_id, date=game.official_date, home=line.team_id == game.home_team_id, stats=line.stats)
                if discipline == 'pitching':
                    item['outs_recorded'] = line.outs_recorded
                item['metrics'] = metrics({**line.stats, **({'outs_recorded': line.outs_recorded} if discipline == 'pitching' else {})}, discipline)
                rows.append(item)
            result[discipline] = rows
    return result

def history_rows(groups):
    result = {}
    for group in groups:
        discipline = 'batting' if group['group']['displayName'] == 'hitting' else 'pitching'
        by_year = defaultdict(list)
        for split in group.get('splits', []):
            if split.get('sport', {}).get('id', 1) == 1:
                by_year[split['season']].append(split)
        rows = []
        for year, splits in by_year.items():
            # MLB may return an all-team total plus stints; never add both.
            total = next((s for s in splits if not s.get('team')), None)
            if total or len(splits) == 1:
                values = normalize_stats((total or splits[0])['stat'], discipline)
            else:
                lines = [{'stats': s['stat'], 'outs_recorded': s['stat'].get('outs', outs(s['stat'].get('inningsPitched', '0.0')))} for s in splits]
                values = aggregate(lines, discipline)
                values['gamesPlayed'] = sum(s['stat'].get('gamesPlayed', 0) for s in splits)
            rows.append(dict(season=int(year), teams=', '.join(dict.fromkeys(s.get('team', {}).get('name', 'All teams') for s in splits)), **values))
        result[discipline] = sorted(rows, key=lambda r: r['season'], reverse=True)
    return result

@router.get('/players/{player_id}')
def player_detail(player_id: int, season: int = Query(2026, ge=1900, le=2100), game_type: str = 'R', window: int = Query(15, ge=7, le=30)):
    if window not in (7, 15, 30):
        raise HTTPException(422, 'Choose a recent-form window of 7, 15, or 30 days.')
    data, stamp, stale = snapshot(f'/people/{player_id}', {'hydrate': 'currentTeam'}, ttl=86400)
    if not data.get('people'):
        raise HTTPException(404, 'Player not found')
    person = data['people'][0]
    official, summary_stamp, summary_stale = snapshot(f'/people/{player_id}/stats', {'stats': 'season', 'group': 'hitting,pitching', 'season': season, 'gameType': game_type, 'sportIds': 1})
    summaries = history_rows(official.get('stats', []))
    season_stats = {d: rows[0] if rows else {} for d, rows in summaries.items()}
    years, history_stamp, history_stale = snapshot(f'/people/{player_id}/stats', {'stats': 'yearByYear', 'group': 'hitting,pitching', 'sportIds': 1, 'gameType': game_type}, ttl=86400)
    logs = local_logs(player_id, season, game_type)
    latest = max((r['date'] for rows in logs.values() for r in rows), default=None)
    since = str(date.fromisoformat(latest) - timedelta(days=window-1)) if latest else None
    recent, splits = {}, {}
    for discipline, rows in logs.items():
        selected = [r for r in rows if since and r['date'] >= since]
        recent[discipline] = {'summary': aggregate(selected, discipline), 'games': selected}
        splits[discipline] = {side: aggregate([r for r in rows if r['home'] == (side == 'home')], discipline) for side in ('home', 'away')}
    return dict(id=player_id, full_name=person['fullName'], position=person.get('primaryPosition', {}).get('abbreviation'), birth_date=person.get('birthDate'), debut_date=person.get('mlbDebutDate'), height=person.get('height'), weight=person.get('weight'), bat_side=person.get('batSide', {}).get('code'), throw_hand=person.get('pitchHand', {}).get('code'), current_team=person.get('currentTeam'), season=season, game_type=game_type, season_stats=season_stats, history=history_rows(years.get('stats', [])), logs=logs, recent=recent, splits=splits, recent_as_of=latest, recent_since=since, window=window, coverage=coverage(season, game_type), fetched_at=summary_stamp, stale=stale or summary_stale or history_stale, source='MLB official season summaries; game logs, form and home/away splits use imported games only')

@router.get('/games/{game_id}')
def explore_game(game_id: int):
    # Unimported team-schedule games remain inspectable during the backfill.
    from .main import game_detail as stored_game
    with Session() as session:
        exists = session.get(Game, game_id)
    if exists and exists.last_synced_at:
        return stored_game(game_id)
    data, stamp, stale = snapshot('/schedule', {'gamePk': game_id})
    raw = next((g for day in data.get('dates', []) for g in day['games']), None)
    if not raw:
        raise HTTPException(404, 'Game not found')
    box, stamp, stale = snapshot(f'/game/{game_id}/boxscore')
    result = dict(id=game_id, season=int(raw['season']), game_type=raw['gameType'], official_date=raw['officialDate'], status=raw['status']['detailedState'], home_team_id=raw['teams']['home']['team']['id'], away_team_id=raw['teams']['away']['team']['id'], batting=[], pitching=[])
    for side in ('home', 'away'):
        club = box.get('teams', {}).get(side, {})
        for person in club.get('players', {}).values():
            for discipline in ('batting', 'pitching'):
                values = person.get('stats', {}).get(discipline, {})
                if values:
                    result[discipline].append(dict(game_id=game_id, player_id=person['person']['id'], player_name=person['person']['fullName'], team_id=club['team']['id'], date=raw['officialDate'], stats=values, outs_recorded=outs(values.get('inningsPitched', '0.0')) if discipline == 'pitching' else None))
    return result

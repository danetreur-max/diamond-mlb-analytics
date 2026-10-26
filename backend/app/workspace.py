"""Exploration helpers: comparable season deltas and explicit refresh jobs."""
from datetime import datetime, date, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func
from .db import Session, Game, Run
from .explore import season_splits, normalize_stats, snapshot, coverage, aggregate

from .patterns import pattern_summary
from .explore import local_logs

router = APIRouter(prefix='/api/workspace', tags=['Workspace'])

def sample_matches(row, min_pa=0, min_ip=0, min_age=None, max_age=None, pitcher_role='all'):
    if (row.get('plateAppearances') or 0) < min_pa or (row.get('outs_recorded') or 0) < min_ip * 3:
        return False
    age = row.get('age')
    if min_age is not None and (age is None or age < min_age):
        return False
    if max_age is not None and (age is None or age > max_age):
        return False
    # Role is defined by starts versus appearances, not roster position.
    games, starts = row.get('gamesPlayed') or 0, row.get('gamesStarted') or 0
    if pitcher_role == 'starter' and not (games and starts / games >= .5):
        return False
    if pitcher_role == 'reliever' and not (games and starts / games < .5):
        return False
    return True

def change_stats(stats, discipline):
    result = normalize_stats(stats, discipline)
    return result

def season_changes(before, after, metric, minimum, discipline):
    old = {s['player']['id']: change_stats(s['stat'], discipline) for s in before}
    rows = []
    sample = 'plateAppearances' if discipline == 'batting' else 'outs_recorded'
    for split in after:
        pid = split['player']['id']
        current, previous = change_stats(split['stat'], discipline), old.get(pid)
        if previous is None or min(current.get(sample) or 0, previous.get(sample) or 0) < minimum:
            continue
        a, b = previous.get(metric), current.get(metric)
        if a is None or b is None:
            continue
        delta = b - a
        rows.append(dict(id=pid, full_name=split['player']['fullName'], before=a, after=b, delta=delta,
                         improvement=-delta if metric in ('era','whip','bb9','hr9') or (discipline == 'pitching' and metric == 'bbPct') or (discipline == 'batting' and metric == 'kPct') else delta,
                         before_sample=previous.get(sample, 0), after_sample=current.get(sample, 0), pattern=pattern_summary(previous,current,discipline)))
    return rows

@router.get('/changes')
def changes(season: int = Query(2026, ge=1901, le=2100), discipline: str = 'batting', game_type: str = 'R', metric: str = 'ops', minimum: int = Query(100, ge=0), direction: str = 'improved', qualified: bool = False):
    allowed = {'batting': ('iso','babip','kPct','bbPct','avg','obp','slg','ops','gamesPlayed','plateAppearances','hits','homeRuns','rbi','runs','stolenBases','baseOnBalls','strikeOuts'), 'pitching': ('kPct','bbPct','kMinusBbPct','era','whip','k9','bb9','hr9','kbb','gamesPlayed','gamesStarted','outs_recorded','wins','losses','saves','strikeOuts','baseOnBalls','hits','homeRuns','earnedRuns')}
    if discipline not in allowed or metric not in allowed[discipline] or direction not in ('improved','declined'):
        raise HTTPException(422, 'Choose a valid discipline, statistic and direction.')
    if qualified and game_type != 'R':
        raise HTTPException(422, 'Qualification is available for the regular season only.')
    before, old_stamp, old_stale = season_splits(season-1, discipline, game_type, qualified)
    after, stamp, stale = season_splits(season, discipline, game_type, qualified)
    rows = season_changes(before, after, metric, minimum if discipline == 'batting' else minimum*3, discipline)
    rows.sort(key=lambda r: r['improvement'], reverse=direction == 'improved')
    return dict(items=rows, previous_season=season-1, season=season, metric=metric, fetched_at=stamp,
                previous_fetched_at=old_stamp, stale=stale or old_stale)

@router.get('/freshness')
def freshness(season: int = 2026, game_type: str = 'R'):
    with Session() as session:
        latest = session.scalar(select(func.max(Game.official_date)).where(Game.last_synced_at.is_not(None), Game.season == season, Game.game_type == game_type))
        synced = session.scalar(select(func.max(Game.last_synced_at)))
        active = session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(['queued','running'])))
        successful = session.scalar(select(Run).where(Run.status == 'completed').order_by(Run.finished_at.desc()).limit(1))
        last_refresh = successful.finished_at if successful else None
    audit = coverage(season, game_type)
    return dict(latest_game_date=latest, last_synced_at=synced, active_jobs=active, last_successful_refresh=last_refresh, coverage=audit, missing_games=max(0,audit['expected_games']-audit['imported_games']))

def matchup_rows(groups, discipline):
    rows = {}
    for group in groups:
        for item in group.get('splits', []):
            code = item.get('split', {}).get('code')
            if code not in ('vl','vr'):
                continue
            # MLB supplies combined and club-specific rows for traded players.
            # Prefer the combined record; never sum an aggregate with stints.
            rows.setdefault(code, []).append(item)
    opponent = 'pitchers' if discipline == 'batting' else 'batters'
    result = []
    for code, items in sorted(rows.items()):
        total = next((item for item in items if not item.get('team')), None)
        if total or len(items) == 1:
            stats = normalize_stats((total or items[0])['stat'], discipline)
        else:
            normalized = [normalize_stats(item['stat'], discipline) for item in items]
            stats = aggregate([{'stats': item, 'outs_recorded': item.get('outs_recorded',0)} for item in normalized], discipline)
        result.append(dict(code=code, label=f"vs {'left' if code=='vl' else 'right'}-handed {opponent}", stats=stats))
    return result

@router.get('/players/{player_id}/matchups')
def matchups(player_id: int, season: int = Query(2026, ge=1900, le=2100), game_type: str = 'R', discipline: str = 'batting'):
    if discipline not in ('batting','pitching'):
        raise HTTPException(422, 'Choose batting or pitching.')
    data, stamp, stale = snapshot(f'/people/{player_id}/stats', {'stats':'statSplits','group':'hitting' if discipline=='batting' else 'pitching','season':season,'gameType':game_type,'sitCodes':'vl,vr','sportIds':1})
    return dict(items=matchup_rows(data.get('stats',[]),discipline), fetched_at=stamp, stale=stale)

def refresh_range(latest, today):
    # Revisit seven days for corrections and catch up in bounded monthly chunks.
    start = max(date(today.year, 1, 1), date.fromisoformat(latest) - timedelta(days=6)) if latest else today-timedelta(days=6)
    start = min(start, today)
    return start, min(today, start+timedelta(days=30))

@router.post('/refresh', status_code=202)
def refresh():
    # Explicit offset avoids depending on the server's timezone or tzdata install.
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('America/Los_Angeles')).date()
    with Session.begin() as session:
        if session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(['queued','running']))):
            raise HTTPException(409, 'An import is already queued or running. Check Imports before refreshing again.')
        latest = session.scalar(select(func.max(Game.official_date)).where(Game.last_synced_at.is_not(None)))
        start, end = refresh_range(latest, today)
        run = Run(start_date=str(start), end_date=str(end))
        session.add(run)
        session.flush()
        return dict(id=run.id, start_date=run.start_date, end_date=run.end_date, status=run.status,
                    more_remaining=end < today)


@router.get('/players/{player_id}/period-changes')
def period_changes(player_id: int, season: int = Query(2026, ge=1901, le=2100), game_type: str = 'R', discipline: str = 'batting', appearances: int = Query(30, ge=5, le=100)):
    if discipline not in ('batting','pitching'):
        raise HTTPException(422,'Choose batting or pitching.')
    audits=[coverage(year,game_type) for year in (season-1,season)]
    if not all(a['complete'] for a in audits):
        return dict(available=False,reason='Complete imported game coverage is required in both seasons to identify the first appearances reliably.',coverage=audits)
    periods=[]
    for year in (season-1,season):
        logs=local_logs(player_id,year,game_type)[discipline]
        logs=[r for r in logs if (r['metrics'].get('plateAppearances',0)>0 if discipline=='batting' else r['metrics'].get('outs_recorded',0)>0 or r['stats'].get('battersFaced',0)>0)]
        # Select complete appearances. Never prorate or split a game to hit a PA/IP target.
        if len(logs)<appearances:
            return dict(available=False,reason=f'{year} has only {len(logs)} imported {discipline} appearances; choose a smaller period.')
        selected=logs[:appearances]
        periods.append(dict(season=year,start=selected[0]['date'],end=selected[-1]['date'],appearances=appearances,stats=aggregate(selected,discipline)))
    return dict(available=True,periods=periods,pattern=pattern_summary(periods[0]['stats'],periods[1]['stats'],discipline),stale=any(a['stale'] for a in audits),note='First N complete player appearances in each season, ordered by official game date and game ID. Equal appearances do not guarantee equal PA or innings. These are imported game totals, not the official season summaries.')

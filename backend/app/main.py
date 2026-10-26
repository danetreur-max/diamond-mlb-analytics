import os
import subprocess
import sys
from contextlib import asynccontextmanager
from .deployment import public_demo, protect_public_demo
from datetime import date
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import select, func, or_
from .player_stats import summary as stat_summary, breakdown
from .enrichment_api import router as enrichment_router
from .enrichment import PlayerSummary
from .db import Session, Team, Player, Game, Batting, Pitching, Run, init_db

@asynccontextmanager
async def lifespan(app):
    init_db()
    if os.getenv('MLB_START_WORKER', '1') != '0' and not public_demo():
        subprocess.Popen([sys.executable, '-m', 'app.worker'], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    yield

app = FastAPI(title='MLB Explorer API', lifespan=lifespan, docs_url=None if public_demo() else '/docs', redoc_url=None if public_demo() else '/redoc')
app.middleware('http')(protect_public_demo)

@app.get('/api/deployment')
def deployment_mode():
    return {'read_only':public_demo(),'mode':'Public read-only demo' if public_demo() else 'Local research workspace'}
app.include_router(enrichment_router)

def serialize(obj):
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}

class ImportRequest(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode='after')
    def valid_range(self):
        if not 0 <= (self.end_date - self.start_date).days <= 30:
            raise ValueError('Choose an ordered date range of at most 31 days.')
        if self.end_date > date.today():
            raise ValueError('Imports cannot end in the future.')
        return self

@app.get('/api/health')
def health():
    with Session() as session:
        session.execute(select(1))
    return {'status': 'ok'}

@app.get('/api/summary')
def summary():
    with Session() as session:
        return {**{cls.__tablename__: session.scalar(select(func.count()).select_from(cls)) for cls in (Team, Player, Game, Batting, Pitching)}, 'latest_season': session.scalar(select(func.max(Game.season)))}

@app.get('/api/teams')
def teams():
    with Session() as session:
        return [serialize(row) for row in session.scalars(select(Team).order_by(Team.name))]

@app.get('/api/players')
def players(q: str = '', season: int | None = None, game_type: str = 'R', role: str = Query('all', pattern='^(all|batting|pitching)$'), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    with Session() as session:
        query = select(Player).where(Player.full_name.ilike('%' + q + '%'))
        def participation(cls):
            stmt = select(cls.player_id).join(Game, cls.game_id == Game.id).where(Game.game_type == game_type)
            return stmt.where(Game.season == season) if season is not None else stmt
        kinds = [Batting, Pitching] if role == 'all' else [Batting if role == 'batting' else Pitching]
        query = query.where(or_(*(Player.id.in_(participation(cls)) for cls in kinds)))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        selected = list(session.scalars(query.order_by(Player.full_name, Player.id).offset(offset).limit(limit)))
        ids = [p.id for p in selected]
        grouped = {}
        for name, cls in [('batting', Batting), ('pitching', Pitching)]:
            grouped[name] = {id: [] for id in ids}
            condition = cls.player_id.in_(ids) & (Game.game_type == game_type)
            if season is not None:
                condition = condition & (Game.season == season)
            for row in lines(session, cls, condition):
                grouped[name][row['player_id']].append(row)
        items = []
        for player in selected:
            item = serialize(player)
            for name in grouped:
                item[name] = stat_summary(grouped[name][player.id], name == 'pitching')
            item['team_ids'] = sorted({r['team_id'] for name in grouped for r in grouped[name][player.id]})
            items.append(item)
        return {'total': total, 'items': items}

@app.get('/api/games')
def games(start_date: date | None = None, end_date: date | None = None, team_id: int | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    query = select(Game)
    if start_date:
        query = query.where(Game.official_date >= str(start_date))
    if end_date:
        query = query.where(Game.official_date <= str(end_date))
    if team_id:
        query = query.where(or_(Game.home_team_id == team_id, Game.away_team_id == team_id))
    with Session() as session:
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return {'total': total, 'items': [serialize(g) for g in session.scalars(query.order_by(Game.official_date.desc(), Game.id).offset(offset).limit(limit))]}

def lines(session, cls, condition):
    result = []
    for stat, player, game in session.execute(select(cls, Player, Game).join(Player, cls.player_id == Player.id).join(Game, cls.game_id == Game.id).where(condition).order_by(Game.official_date.desc(), cls.player_id)):
        result.append({**serialize(stat), 'player_name': player.full_name, 'date': game.official_date, 'game_type': game.game_type, 'season': game.season, 'venue': 'home' if stat.team_id == game.home_team_id else 'away', 'opponent_id': game.away_team_id if stat.team_id == game.home_team_id else game.home_team_id})
    return result

@app.get('/api/games/{game_id}')
def game_detail(game_id: int):
    with Session() as session:
        game = session.get(Game, game_id)
        if not game:
            raise HTTPException(404, 'Game not found')
        return {**serialize(game), 'batting': lines(session, Batting, Batting.game_id == game_id), 'pitching': lines(session, Pitching, Pitching.game_id == game_id)}

@app.get('/api/players/{player_id}')
def player_detail(player_id: int, season: int | None = None, game_type: str = 'R'):
    with Session() as session:
        player = session.get(Player, player_id)
        if not player:
            raise HTTPException(404, 'Player not found')
        result = serialize(player)
        all_records = {name: lines(session, cls, (cls.player_id == player_id) & (Game.game_type == game_type))
                       for name, cls in [('batting', Batting), ('pitching', Pitching)]}
        result['seasons'] = sorted({r['season'] for rows in all_records.values() for r in rows}, reverse=True)
        source_competition = 'P' if game_type in ('F', 'D', 'L', 'W') else 'R'
        result['source_seasons'] = list(session.scalars(select(PlayerSummary.season).where(PlayerSummary.player_id == player_id, PlayerSummary.competition == source_competition, PlayerSummary.season.is_not(None)).distinct().order_by(PlayerSummary.season.desc())))
        selected = {name: [r for r in rows if season is None or r['season'] == season] for name, rows in all_records.items()}
        as_of = max((r['date'] for rows in selected.values() for r in rows), default=None)
        result['as_of'] = as_of
        for name, records in selected.items():
            result[name] = breakdown(records, name == 'pitching', as_of)
        result['career'] = [{'season': year, **{name: stat_summary([r for r in rows if r['season'] == year], name == 'pitching')
                            for name, rows in all_records.items()}} for year in result['seasons']]
        result['team_ids'] = sorted({r['team_id'] for rows in selected.values() for r in rows})
        result['coverage_note'] = 'Imported games only â€” not verified complete-season or career totals.'
        return result

@app.post('/api/imports', status_code=202)
def create_import(request: ImportRequest):
    with Session.begin() as session:
        active = session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(['queued', 'running'])))
        if active >= 10:
            raise HTTPException(409, 'Import queue is full; wait for current imports to finish.')
        run = Run(start_date=str(request.start_date), end_date=str(request.end_date))
        session.add(run)
        session.flush()
        result = serialize(run)
    return result

@app.get('/api/imports')
def imports():
    with Session() as session:
        return [serialize(r) for r in session.scalars(select(Run).order_by(Run.id.desc()).limit(50))]

from .explore import router as explorer_router
app.include_router(explorer_router)

from .workspace import router as workspace_router
app.include_router(workspace_router)

from .contracts import router as contracts_router
app.include_router(contracts_router)

from .contract_intelligence import router as intelligence_router
app.include_router(intelligence_router)


# Optional same-origin production frontend; keep API routes registered first.
if os.getenv('MLB_FRONTEND_DIR'):
    from fastapi.staticfiles import StaticFiles
    app.mount('/',StaticFiles(directory=os.environ['MLB_FRONTEND_DIR'],html=True),name='frontend')

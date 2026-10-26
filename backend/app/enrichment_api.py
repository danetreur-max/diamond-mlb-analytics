import json
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func, create_engine
from sqlalchemy.orm import sessionmaker
from .db import Session, Player, Run
from .enrichment import PlayerSummary, SummaryFetch, SavantSummary, SavantDetail

router = APIRouter(prefix='/api', tags=['Backfill and source summaries'])
# A primary preview may read source summaries from the independent backfill
# until final publication, while its core game routes keep their own database.
_source_url = os.getenv('MLB_ENRICHMENT_DATABASE_URL')
SourceSession = sessionmaker(create_engine(_source_url)) if _source_url else None


@router.get('/backfill')
def backfill_status():
    path = Path(os.getenv('MLB_BACKFILL_MANIFEST', 'full-backfill.json'))
    if not path.exists():
        return {'status': 'not_started'}
    plan = json.loads(path.read_text(encoding='utf-8'))
    result = {k: plan.get(k) for k in ['status', 'start_date', 'end_date', 'created_at', 'updated_at', 'finished_at', 'active', 'coverage', 'summary_players_expected', 'summary_requests_expected', 'summaries_complete', 'savant_completed', 'last_error']}
    if result.get('coverage'):
        result['coverage'] = {k: v for k, v in result['coverage'].items() if k != 'missing_game_ids'}
    with (SourceSession or Session)() as session:
        result['summary_requests_complete'] = session.scalar(select(func.count()).select_from(SummaryFetch))
        result['source_summary_rows'] = session.scalar(select(func.count()).select_from(PlayerSummary))
        result['statcast_rows'] = session.scalar(select(func.count()).select_from(SavantSummary)) + session.scalar(select(func.count()).select_from(SavantDetail))
        if (plan.get('active') or {}).get('run_id'):
            run = session.get(Run, plan['active']['run_id'])
            if run:
                result['active_run'] = {k: getattr(run, k) for k in ['id', 'status', 'total_games', 'processed_games', 'heartbeat_at']}
    return result


@router.get('/players/{player_id}/source-stats')
def source_stats(player_id: int):
    with (SourceSession or Session)() as session:
        if session.get(Player, player_id) is None:
            raise HTTPException(404, 'Player not found')
        rows = session.scalars(select(PlayerSummary).where(PlayerSummary.player_id == player_id).order_by(PlayerSummary.competition, PlayerSummary.discipline, PlayerSummary.season.desc(), PlayerSummary.team_id, PlayerSummary.key))
        stats = [{c.name: getattr(row, c.name) for c in PlayerSummary.__table__.columns} for row in rows]
        savant = session.scalars(select(SavantSummary).where(SavantSummary.player_id == player_id).order_by(SavantSummary.season.desc(), SavantSummary.role))
        details = session.scalars(select(SavantDetail).where(SavantDetail.player_id == player_id).order_by(SavantDetail.season.desc(), SavantDetail.dataset))
        statcast = [{**{c.name: getattr(row, c.name) for c in SavantSummary.__table__.columns}, 'dataset': 'expected_statistics'} for row in savant]
        statcast += [{c.name: getattr(row, c.name) for c in SavantDetail.__table__.columns} for row in details]
        return {'source': 'MLB Stats API', 'rows': stats, 'statcast': statcast,
                'note': 'Source summaries are fetched independently of local game coverage. R = regular season; P = combined postseason. Team stints and total rows must not be added together. Statcast leaderboard summaries cover the regular season.'}

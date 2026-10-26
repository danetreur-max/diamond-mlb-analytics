"""Independent source summaries; never mixed with imported-game aggregates."""
import csv
import hashlib
import io
import json
from sqlalchemy import ForeignKey, JSON, delete
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, Session, Payload, now, upsert
from .ingest import fetch


class PlayerSummary(Base):
    __tablename__ = 'player_stat_summaries'
    key: Mapped[str] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey('players.id'), index=True)
    competition: Mapped[str]
    discipline: Mapped[str]
    scope: Mapped[str]
    season: Mapped[int | None]
    team_id: Mapped[int | None]
    position: Mapped[str]
    stats: Mapped[dict] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[str]


class SummaryFetch(Base):
    __tablename__ = 'player_summary_fetches'
    player_id: Mapped[int] = mapped_column(ForeignKey('players.id'), primary_key=True)
    competition: Mapped[str] = mapped_column(primary_key=True)
    fetched_at: Mapped[str]
    row_count: Mapped[int]


class SavantSummary(Base):
    __tablename__ = 'savant_season_summaries'
    season: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(primary_key=True)
    stats: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[str]


class SavantDetail(Base):
    __tablename__ = 'savant_season_details'
    key: Mapped[str] = mapped_column(primary_key=True)
    dataset: Mapped[str]
    season: Mapped[int]
    role: Mapped[str]
    player_id: Mapped[int] = mapped_column(index=True)
    stats: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[str]


def summary_rows(player_id, competition, payload, fetched_at):
    rows = {}
    for block in payload.get('stats', []):
        scope = block['type']['displayName']
        if scope not in ('yearByYear', 'career'):
            continue
        for split in block.get('splits', []):
            if split.get('sport', {}).get('id') != 1 or split.get('gameType') != competition:
                continue
            metadata = {k: v for k, v in split.items() if k != 'stat'}
            identity = {'player_id': player_id, 'competition': competition, 'scope': scope,
                        'discipline': block['group']['displayName'], 'metadata': metadata}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            rows[key] = dict(key=key, player_id=player_id, competition=competition, scope=scope,
                             discipline=identity['discipline'], season=int(split['season']) if split.get('season') else None,
                             team_id=split.get('team', {}).get('id'), position=split.get('position', {}).get('abbreviation', ''),
                             stats=split['stat'], metadata_json=metadata, fetched_at=fetched_at)
    return list(rows.values())


def ingest_player_summary(client, player_id, competition):
    path = f'/people/{player_id}/stats'
    params = {'stats': 'yearByYear,career', 'group': 'hitting,pitching,fielding', 'sportIds': 1, 'gameType': competition}
    payload = fetch(client, path, params)
    return store_player_summary(player_id, competition, payload, path, params)


def store_player_summary(player_id, competition, payload, path, params):
    if not isinstance(payload.get('stats'), list):
        raise ValueError('MLB returned no stats collection')
    fetched_at = now()
    rows = summary_rows(player_id, competition, payload, fetched_at)
    endpoint = f'summary:{player_id}:{competition}'
    with Session.begin() as session:
        session.execute(delete(PlayerSummary).where(PlayerSummary.player_id == player_id, PlayerSummary.competition == competition))
        session.add_all(PlayerSummary(**r) for r in rows)
        upsert(session, Payload, endpoint, dict(endpoint=endpoint, fetched_at=fetched_at,
               payload={'source': 'MLB Stats API', 'path': path, 'params': params, 'response': payload}))
        upsert(session, SummaryFetch, (player_id, competition), dict(player_id=player_id, competition=competition, fetched_at=fetched_at, row_count=len(rows)))
    return len(rows)


def ingest_player_batch(client, player_ids, competition):
    hydrate = f'stats(group=[hitting,pitching,fielding],type=[yearByYear,career],sportIds=[1],gameType={competition})'
    params = {'personIds': ','.join(map(str, player_ids)), 'hydrate': hydrate}
    payload = fetch(client, '/people', params)
    people = {p['id']: p for p in payload.get('people', [])}
    missing = set(player_ids) - people.keys()
    for pid in player_ids:
        if pid in people:
            store_player_summary(pid, competition, {'stats': people[pid].get('stats', [])}, '/people', params)
    if missing:
        raise ValueError(f'MLB omitted requested players: {sorted(missing)}')
    return len(player_ids)


def ingest_savant(client, season, role, dataset='expected_statistics'):
    if dataset not in ('expected_statistics', 'statcast', 'pitch-arsenal-stats'):
        raise ValueError('Unsupported Savant dataset')
    url = 'https://baseballsavant.mlb.com/leaderboard/' + dataset
    params = {'type': role, 'year': season, 'position': '', 'team': '', 'filterType': 'bip', 'min': 0, 'csv': 'true'}
    response = client.get(url, params=params, timeout=90)
    response.raise_for_status()
    text = response.content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or 'player_id' not in reader.fieldnames:
        raise ValueError('Unexpected Savant CSV schema; refusing to save HTML or malformed data')
    rows = list(reader)
    if not rows:
        raise ValueError('Empty Savant season export')
    fetched_at = now()
    parsed = {}
    for row in rows:
        pid = int(row['player_id'])
        if row.get('year') and int(row['year']) != season:
            raise ValueError('Savant returned a different season')
        key = pid if dataset == 'expected_statistics' else hashlib.sha256(json.dumps([dataset, season, role, pid, row.get('team_name_alt'), row.get('pitch_type')]).encode()).hexdigest()
        if key in parsed:
            raise ValueError(f'Duplicate Savant player {pid}')
        parsed[key] = dict(season=season, role=role, player_id=pid, stats=row, fetched_at=fetched_at)
        if dataset != 'expected_statistics':
            parsed[key].update(key=key, dataset=dataset)
    endpoint = f'savant:{dataset}:{season}:{role}'
    with Session.begin() as session:
        if dataset == 'expected_statistics':
            session.execute(delete(SavantSummary).where(SavantSummary.season == season, SavantSummary.role == role))
            session.add_all(SavantSummary(**r) for r in parsed.values())
        else:
            session.execute(delete(SavantDetail).where(SavantDetail.dataset == dataset, SavantDetail.season == season, SavantDetail.role == role))
            session.add_all(SavantDetail(**r) for r in parsed.values())
        upsert(session, Payload, endpoint, dict(endpoint=endpoint, fetched_at=fetched_at,
               payload={'source': url, 'params': params, 'csv': text}))
    return len(parsed)

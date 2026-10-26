import json
import httpx
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base, Player, Team, Game, Batting, Pitching, Payload
from app import full_backfill, enrichment
from app.enrichment import PlayerSummary, SummaryFetch, SavantDetail


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path/'backfill.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(full_backfill, 'Session', factory)
    monkeypatch.setattr(enrichment, 'Session', factory)
    with factory.begin() as s:
        s.add_all([Player(id=5,full_name='Five'), Player(id=6,full_name='Six')])
        s.add_all([Team(id=i,name=str(i),abbreviation=str(i),league='',division='') for i in [1,2]])
    return factory


def test_summary_grain_and_competition_guard(database):
    split = {'season':'2025','gameType':'R','sport':{'id':1},'team':{'id':1},'stat':{'hits':2}}
    payload = {'stats':[{'type':{'displayName':'yearByYear'},'group':{'displayName':'hitting'},'splits':[split,split,{**split,'team':{'id':2}}, {**split,'gameType':'P'}, {**split,'sport':{'id':11}}]}]}
    rows = enrichment.summary_rows(5, 'R', payload, '2026-10-10T00:00:00+00:00')
    assert len(rows) == 2
    assert {r['team_id'] for r in rows} == {1,2}
    for _ in range(2):
        assert enrichment.store_player_summary(5,'R',payload,'/people/5/stats',{}) == 2
    with database() as s:
        assert s.scalar(select(func.count()).select_from(PlayerSummary)) == 2
        assert s.get(SummaryFetch,(5,'R')).row_count == 2


def test_batched_empty_summaries_and_missing_player(database):
    handler = lambda request: httpx.Response(200,json={'people':[{'id':5,'stats':[]}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert enrichment.ingest_player_batch(client,[5],'R') == 1
        with pytest.raises(ValueError,match='omitted'):
            enrichment.ingest_player_batch(client,[5,6],'P')
    with database() as s:
        assert s.get(SummaryFetch,(5,'R')).row_count == 0
        assert s.get(SummaryFetch,(6,'P')) is None


def test_coverage_requires_both_team_lines_raw_payload_and_matching_scores(database):
    expected = {'10':{'state':'Final','season':2025,'type':'R','home_score':3,'away_score':1}, '11':{'state':'Preview','status':'Postponed'}}
    assert full_backfill.audit(expected)['missing_game_ids'] == [10]
    with database.begin() as s:
        s.add(Game(id=10,season=2025,game_type='R',official_date='2025-06-01',scheduled_at='2025-06-01',status='Final',home_team_id=1,away_team_id=2,home_score=3,away_score=1,last_synced_at='now'))
        s.flush()
        for pid, team in [(5,1),(6,2)]:
            s.add(Batting(game_id=10,player_id=pid,team_id=team,stats={'hits':1}))
            s.add(Pitching(game_id=10,player_id=pid,team_id=team,outs_recorded=27,stats={}))
    assert full_backfill.audit(expected)['missing_game_ids'] == [10]
    with database.begin() as s:
        s.add(Payload(endpoint='/game/10/boxscore',payload={}))
    report = full_backfill.audit(expected)
    assert report['final_complete'] == 1
    assert report['nonfinal_by_status'] == {'Postponed':1}
    expected['10']['home_score'] = 4
    assert full_backfill.audit(expected)['missing_game_ids'] == [10]


def test_savant_arsenal_preserves_pitch_types_and_rejects_html(database):
    csv = 'player_id,team_name_alt,pitch_type,pitches\n5,T,FF,100\n5,T,SL,50\n'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,text=csv))) as c:
        for _ in range(2):
            assert enrichment.ingest_savant(c,2025,'pitcher','pitch-arsenal-stats') == 2
    with database() as s:
        assert s.scalar(select(func.count()).select_from(SavantDetail)) == 2
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,text='<html>blocked</html>'))) as c:
        with pytest.raises(ValueError,match='schema'):
            enrichment.ingest_savant(c,2025,'batter')


def test_manifest_checkpoint_is_valid_json(tmp_path):
    path = tmp_path/'manifest.json'
    full_backfill.save(path, {'status':'running'})
    assert json.loads(path.read_text())['status'] == 'running'
    assert not path.with_suffix('.tmp').exists()


def test_checkpoint_retries_windows_reader_lock(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.replace
    attempts = []
    def replace(path, target):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError('Temporary Windows sharing conflict')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(full_backfill.time, 'sleep', lambda _: None)
    path = tmp_path/'checkpoint.json'
    full_backfill.save(path, {'status':'running'})
    assert len(attempts) == 3
    assert json.loads(path.read_text())['status'] == 'running'


def test_source_summary_api_and_seasons_without_local_games(database, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, enrichment_api
    monkeypatch.setattr(main, 'Session', database)
    monkeypatch.setattr(enrichment_api, 'Session', database)
    payload={'stats':[{'type':{'displayName':'yearByYear'},'group':{'displayName':'hitting'},'splits':[{'season':'2019','gameType':'R','sport':{'id':1},'stat':{'hits':100}}]}]}
    enrichment.store_player_summary(5,'R',payload,'/people/5/stats',{})
    client=TestClient(main.app)
    profile=client.get('/api/players/5?season=2019').json()
    assert profile['source_seasons'] == [2019]
    assert profile['seasons'] == []
    assert profile['batting']['game_count'] == 0
    source=client.get('/api/players/5/source-stats').json()
    assert source['rows'][0]['stats']['hits'] == 100
    assert client.get('/api/players/999/source-stats').status_code == 404


def test_cancelled_abstract_finals_are_not_played_games(database):
    expected = {"1": {"state": "Final", "status": "Cancelled"},
                "2": {"state": "Final", "status": "Final", "season": 2020, "type": "R", "home_score": 1, "away_score": 0}}
    report = full_backfill.audit(expected)
    assert report["final_expected"] == 1
    assert report["missing_game_ids"] == [2]
    assert report["nonfinal_by_status"] == {"Cancelled": 1}

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.db import Base, Run, Game, Batting
from app import ingest, main

def test_full_pipeline_partial_failure_and_retry(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path/'test.db'), connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(ingest,'Session',factory)
    monkeypatch.setattr(main,'Session',factory)
    monkeypatch.setattr(ingest.time,'sleep',lambda _:None)
    failing = True
    def fake_fetch(client,path,params=None):
        if path == '/teams':
            return {'teams':[{'id':1,'name':'One'},{'id':2,'name':'Two'}]}
        if path == '/people':
            return {'people':[{'id':5,'fullName':'Test Player','batSide':{'code':'L'},'pitchHand':{'code':'R'},'primaryPosition':{'abbreviation':'SS'}}]}
        if path == '/schedule':
            return {'dates':[{'games':[{'gamePk':gid,'season':'2025','gameType':'R','officialDate':'2025-06-01','gameDate':'2025-06-01T18:00:00Z','status':{'abstractGameState':'Final','detailedState':'Final'},'teams':{'home':{'team':{'id':1},'score':3},'away':{'team':{'id':2},'score':1}}} for gid in [10,11]]}]}
        if failing and path == '/game/11/boxscore':
            raise RuntimeError('Simulated network failure')
        return {'teams':{'home':{'team':{'id':1},'players':{'ID5':{'person':{'id':5,'fullName':'Test Player'},'stats':{'batting':{'atBats':4,'hits':2}}}}},'away':{'team':{'id':2},'players':{}}}}
    monkeypatch.setattr(ingest,'fetch',fake_fetch)
    client = TestClient(main.app)  # No lifespan: worker is driven deterministically below.
    assert client.post('/api/imports',json={'start_date':'2025-06-02','end_date':'2025-06-01'}).status_code == 422
    for expected in ['partial','completed']:
        response = client.post('/api/imports',json={'start_date':'2025-06-01','end_date':'2025-06-01'})
        assert response.status_code == 202
        ingest.run_import(response.json()['id'])
        with factory() as session:
            run = session.get(Run,response.json()['id'])
            assert run.status == expected
            assert run.processed_games == (1 if failing else 2)
        failing = False
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Game)) == 2
        assert session.scalar(select(func.count()).select_from(Batting)) == 2
    assert client.get('/api/games?team_id=2').json()['total'] == 2
    assert len(client.get('/api/games/10').json()['batting']) == 1
    assert client.get('/api/players/5?season=2025').json()['batting']['totals']['hits'] == 4
    assert client.get('/api/players/5').json()['bat_side'] == 'L'
    assert client.get('/api/players/5?season=2024').json()['batting']['totals'] == {}
    assert client.get('/api/games/999').status_code == 404

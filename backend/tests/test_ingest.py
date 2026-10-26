import httpx
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.db import Base, Team, Game, Batting, Pitching, Player
from app.ingest import outs, store_box, fetch
from app.main import ImportRequest

def test_baseball_innings():
    assert outs('5.2') == 17
    assert outs('0.1') == 1
    with pytest.raises(ValueError):
        outs('5.3')

def test_atomic_reimport_and_corrections():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Team(id=1,name='Team',abbreviation='T',league='L',division='D'))
        game = Game(id=10,season=2025,game_type='R',official_date='2025-06-01',scheduled_at='2025-06-01',status='Final',home_team_id=1,away_team_id=1)
        session.add(game)
        session.commit()
        entry = {'person':{'id':5,'fullName':'Player'},'stats':{'batting':{'atBats':4,'hits':2,'avg':'.500'},'pitching':{'inningsPitched':'5.2','strikeOuts':7}}}
        box = {'teams':{'home':{'team':{'id':1},'players':{'ID5':entry}},'away':{'team':{'id':1},'players':{}}}}
        store_box(session,game,box); session.commit()
        store_box(session,game,box); session.commit()
        assert session.scalar(select(func.count()).select_from(Batting)) == 1
        assert session.get(Pitching,(10,5,1)).outs_recorded == 17
        assert 'avg' not in session.get(Batting,(10,5,1)).stats
        entry['stats']['batting']['hits'] = 3
        store_box(session,game,box); session.commit()
        assert session.get(Batting,(10,5,1)).stats['hits'] == 3
        entry['stats']['pitching']['inningsPitched'] = '5.7'
        with pytest.raises(ValueError):
            store_box(session,game,box)
        session.rollback()
        assert session.get(Pitching,(10,5,1)).outs_recorded == 17
        box['teams']['home']['players'] = {}
        store_box(session,game,box); session.commit()
        assert session.scalar(select(func.count()).select_from(Batting)) == 0

def test_retry_transient_but_not_permanent(monkeypatch):
    monkeypatch.setattr('app.ingest.time.sleep',lambda _:None)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls)<3 else 200,json={'ok':True})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch(client,'/teams') == {'ok':True}
    assert len(calls)==3
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(404))) as client:
        with pytest.raises(httpx.HTTPStatusError): fetch(client,'/missing')


def test_player_appears_for_both_teams_and_legacy_migration(tmp_path):
    from sqlalchemy.schema import CreateTable
    from app.db import migrate_game_keys
    engine = create_engine('sqlite:///' + (tmp_path/'legacy.db').as_posix())
    Base.metadata.create_all(engine)
    # Reconstruct the previous schema to verify the real migration path.
    with engine.begin() as connection:
        for cls in (Batting, Pitching):
            connection.exec_driver_sql(f'DROP TABLE {cls.__tablename__}')
            ddl = str(CreateTable(cls.__table__).compile(engine)).replace('PRIMARY KEY (game_id, player_id, team_id)', 'PRIMARY KEY (game_id, player_id)')
            connection.exec_driver_sql(ddl)
    with Session(engine) as session:
        for tid in (1,2):
            session.add(Team(id=tid,name=str(tid),abbreviation=str(tid),league='',division=''))
        session.add(Player(id=5,full_name='Two clubs'))
        session.add(Game(id=10,season=2024,game_type='R',official_date='2024-06-26',scheduled_at='',status='Final',home_team_id=1,away_team_id=2))
        session.flush()
        session.add(Batting(game_id=10,player_id=5,team_id=1,stats={'hits':1}))
        session.commit()
    migrate_game_keys(engine)
    migrate_game_keys(engine)
    assert len(list(tmp_path.glob('legacy.before-team-key-*.db'))) == 1
    with Session(engine) as session:
        assert session.get(Batting,(10,5,1)).stats['hits'] == 1
        entry = {'person':{'id':5,'fullName':'Two clubs'},'stats':{'batting':{'atBats':1,'hits':1}}}
        box = {'teams':{side:{'team':{'id':tid},'players':{'ID5':entry}} for side,tid in [('home',1),('away',2)]}}
        for _ in range(2):
            store_box(session,session.get(Game,10),box)
            session.commit()
        rows = session.scalars(select(Batting)).all()
        assert len(rows) == 2 and {r.team_id for r in rows} == {1,2}
        assert sum(r.stats['hits'] for r in rows) == 2
    engine.dispose()

def test_date_validation():
    with pytest.raises(ValueError): ImportRequest(start_date='2025-06-02',end_date='2025-06-01')
    with pytest.raises(ValueError): ImportRequest(start_date='2025-01-01',end_date='2025-06-01')

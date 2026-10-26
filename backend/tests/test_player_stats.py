import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app import main
from app.db import Base, Team, Player, Game, Batting, Pitching
from app.player_stats import summary, breakdown


def test_weighted_rates_outs_and_calendar_windows():
    rows = [{'date': '2025-06-01', 'venue': 'home', 'stats': {'atBats': 1, 'hits': 1, 'plateAppearances': 1}},
            {'date': '2025-06-15', 'venue': 'away', 'stats': {'atBats': 9, 'hits': 0, 'plateAppearances': 9}}]
    stats = breakdown(rows, as_of='2025-06-15')
    assert stats['rates']['avg'] == .1
    assert stats['rates']['ops'] == .2
    assert stats['recent']['7']['game_count'] == 1
    assert stats['recent']['15']['game_count'] == 2
    assert stats['splits']['home']['rates']['avg'] == 1
    assert summary([])['rates']['avg'] is None
    assert summary([{'date':'2025-06-15','outs_recorded':17,'stats':{'earnedRuns':2,'hits':3,'baseOnBalls':1,'strikeOuts':7,'battersFaced':21}}], True)['rates']['era'] == pytest.approx(54/17)


def test_filtered_directory_history_and_two_way_player(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path/'players.db'), connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(main, 'Session', factory)
    with factory.begin() as session:
        session.add_all([Team(id=i,name=str(i),abbreviation=str(i),league='',division='') for i in [1,2]])
        session.add_all([Player(id=5,full_name='Two Way'), Player(id=6,full_name='Other')])
        for id, year, kind, day in [(10,2025,'R','2025-06-01'),(11,2025,'R','2025-06-15'),(12,2024,'R','2024-06-15'),(13,2025,'S','2025-03-01')]:
            session.add(Game(id=id,season=year,game_type=kind,official_date=day,scheduled_at=day,status='Final',home_team_id=1,away_team_id=2))
        session.flush()
        for id, team in [(10,1),(11,2),(12,1),(13,1)]:
            session.add(Batting(game_id=id,player_id=5,team_id=team,stats={'atBats':4,'hits':2,'plateAppearances':4,'homeRuns':1}))
        session.add(Pitching(game_id=11,player_id=5,team_id=2,outs_recorded=17,stats={'earnedRuns':2,'battersFaced':21,'strikeOuts':7}))
    client = TestClient(main.app)
    directory = client.get('/api/players?season=2025&role=batting').json()
    assert directory['total'] == 1
    assert directory['items'][0]['team_ids'] == [1,2]
    assert directory['items'][0]['batting']['totals']['hits'] == 4
    assert client.get('/api/players?season=2024&role=pitching').json()['total'] == 0
    assert client.get('/api/players?role=invalid').status_code == 422
    profile = client.get('/api/players/5?season=2025').json()
    assert profile['batting']['game_count'] == 2
    assert profile['pitching']['game_count'] == 1
    assert profile['batting']['splits']['away']['game_count'] == 1
    assert profile['batting']['games'][0]['opponent_id'] == 1
    assert [r['season'] for r in profile['career']] == [2025,2024]
    assert profile['batting']['recent']['7']['game_count'] == 1
    assert client.get('/api/players/5?season=2026').json()['batting']['totals'] == {}

from app.contract_intelligence import validate_benchmark

def test_validation_excludes_target_year_inputs():
    rows=validate_benchmark({1:{2022:1,2023:2,2024:3,2025:99}},[2025])
    expected=(3*5+2*3+1)/9
    assert rows[0]['count']==1
    assert abs(rows[0]['mae']-(99-round(expected,3)))<1e-9
    assert rows[0]['within_one']==0

def test_missing_history_and_target_are_not_zero():
    rows=validate_benchmark({1:{2024:2},2:{2025:1},3:{2023:2,2025:3}},[2025])
    assert rows[0]['count']==1
    assert rows[0]['mae']==1
    assert rows[0]['bias']==1
    assert rows[0]['within_one']==1

def test_empty_validation_is_unavailable():
    row=validate_benchmark({},[2025])[0]
    assert row['count']==0
    assert row['mae'] is None
    assert row['rmse'] is None

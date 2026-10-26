from datetime import date
from app.backfill import chunks

def test_monthly_chunks_cover_range_without_gaps():
    result = list(chunks(date(2025,1,1),date(2026,10,10)))
    assert len(result) == 22
    assert result[0] == (date(2025,1,1),date(2025,1,31))
    assert result[-1] == (date(2026,10,1),date(2026,10,10))
    assert sum((end-start).days+1 for start,end in result) == (date(2026,10,10)-date(2025,1,1)).days+1
    assert all(0 <= (end-start).days <= 30 for start,end in result)

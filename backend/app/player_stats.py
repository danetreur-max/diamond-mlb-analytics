"""Derived statistics from imported counting lines, never source rate averages."""
from datetime import date, timedelta


def summary(records, pitching=False):
    totals = {}
    for row in records:
        for key, value in row['stats'].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    n = lambda key: totals.get(key, 0)
    if pitching:
        outs = sum(row['outs_recorded'] for row in records)
        if records:
            totals['outs_recorded'] = outs
        rates = {'era': ratio(n('earnedRuns') * 27, outs),
                 'whip': ratio((n('hits') + n('baseOnBalls')) * 3, outs),
                 'k_pct': ratio(n('strikeOuts'), n('battersFaced')),
                 'bb_pct': ratio(n('baseOnBalls'), n('battersFaced'))}
    else:
        ab, hits, walks, hbp = n('atBats'), n('hits'), n('baseOnBalls'), n('hitByPitch')
        total_bases = n('totalBases') if 'totalBases' in totals else hits + n('doubles') + 2*n('triples') + 3*n('homeRuns')
        obp = ratio(hits + walks + hbp, ab + walks + hbp + n('sacFlies'))
        slg = ratio(total_bases, ab)
        rates = {'avg': ratio(hits, ab), 'obp': obp, 'slg': slg,
                 'ops': obp + slg if obp is not None and slg is not None else None,
                 'k_pct': ratio(n('strikeOuts'), n('plateAppearances')),
                 'bb_pct': ratio(walks, n('plateAppearances'))}
    return {'totals': totals, 'rates': rates, 'game_count': len(records),
            'first_date': min((r['date'] for r in records), default=None),
            'last_date': max((r['date'] for r in records), default=None)}


def breakdown(records, pitching=False, as_of=None):
    result = summary(records, pitching)
    result['games'] = records
    result['splits'] = {side: summary([r for r in records if r['venue'] == side], pitching)
                        for side in ['home', 'away']}
    result['recent'] = {}
    if as_of:
        for days in [7, 15, 30]:
            start = (date.fromisoformat(as_of) - timedelta(days=days-1)).isoformat()
            result['recent'][str(days)] = summary([r for r in records if start <= r['date'] <= as_of], pitching)
    return result

"""Transparent descriptive season comparisons, not causal or predictive claims."""
from math import isfinite

RULES = {'iso': .030, 'ops': .050, 'kPct': 2, 'bbPct': 2, 'era': .50,
         'whip': .10, 'kMinusBbPct': 3, 'hr9': .25, 'babip': .030}

def pattern_summary(before, after, discipline):
    keys = ['ops','iso','kPct','bbPct','babip'] if discipline == 'batting' else ['era','whip','kPct','bbPct','kMinusBbPct','hr9']
    evidence = []
    for key in keys:
        a,b = before.get(key),after.get(key)
        if isinstance(a,(float,int)) and isinstance(b,(float,int)) and isfinite(a) and isfinite(b):
            evidence.append(dict(metric=key,before=a,after=b,delta=b-a,threshold=RULES[key]))
    moves = {r['metric']: r['delta'] for r in evidence}
    def up(k): return moves.get(k,0) >= RULES[k] - 1e-9
    def down(k): return moves.get(k,0) <= -RULES[k] + 1e-9
    def stable(k): return k in moves and abs(moves[k]) < RULES[k] - 1e-9
    title,kind = 'No large shifts in the available indicators','steady'
    if not evidence:
        title,kind = 'Not enough comparable rate data','unavailable'
    elif discipline == 'batting':
        if up('iso') and up('kPct'): title,kind='More power, more strikeouts','mixed'
        elif up('iso') and down('kPct'): title,kind='More power, fewer strikeouts','aligned'
        elif up('ops') and down('iso'): title,kind='OPS rose while isolated power fell','mixed'
        elif up('iso') and stable('kPct') and stable('bbPct'): title,kind='More power, similar strikeout and walk rates','aligned'
        elif down('iso') and (up('bbPct') or down('kPct')): title,kind='Less power, improved walk or strikeout rate','mixed'
        elif up('bbPct') and down('kPct'): title,kind='More walks, fewer strikeouts','aligned'
        elif down('iso') and up('kPct'): title,kind='Less power, more strikeouts','declining'
        elif up('ops') and up('babip'): title,kind='Higher OPS alongside a higher BABIP','context'
        elif up('iso'): title,kind='Isolated power increased','context'
        elif down('iso'): title,kind='Isolated power decreased','context'
        elif up('kPct'): title,kind='Strikeout rate increased','context'
        elif down('kPct'): title,kind='Strikeout rate decreased','context'
        elif up('ops'): title,kind='OPS increased','context'
        elif down('ops'): title,kind='OPS decreased','context'
    else:
        if down('era') and up('bbPct'): title,kind='Lower ERA, higher walk rate','mixed'
        elif down('era') and down('kMinusBbPct'): title,kind='Lower ERA, smaller strikeout–walk gap','mixed'
        elif up('era') and up('kMinusBbPct'): title,kind='Higher ERA despite a wider strikeout–walk gap','mixed'
        elif down('era') and up('kMinusBbPct'): title,kind='Lower ERA with a wider strikeout–walk gap','aligned'
        elif up('era') and up('hr9'): title,kind='Higher ERA alongside more home runs per inning','declining'
        elif down('bbPct') and up('kPct'): title,kind='More strikeouts, fewer walks per batter faced','aligned'
        elif down('era'): title,kind='Earned-run average decreased','context'
        elif up('era'): title,kind='Earned-run average increased','context'
        elif up('kMinusBbPct'): title,kind='Strikeout–walk gap widened','context'
        elif down('kMinusBbPct'): title,kind='Strikeout–walk gap narrowed','context'
    if kind=='steady':
        shifted=[r for r in evidence if abs(r['delta'])>=r['threshold']-1e-9]
        if shifted:
            names={'ops':'OPS','iso':'Isolated power','kPct':'Strikeout rate','bbPct':'Walk rate','era':'ERA','whip':'WHIP','kMinusBbPct':'Strikeout–walk gap','hr9':'Home runs per nine innings','babip':'BABIP'}
            first=shifted[0]
            title=f"{names[first['metric']]} {'increased' if first['delta']>0 else 'decreased'}"
            kind='context'
    sample='plateAppearances' if discipline=='batting' else 'outs_recorded'
    a,b=before.get(sample),after.get(sample)
    notes=[]
    if a is not None and b is not None:
        if min(a,b)<(200 if discipline=='batting' else 150):
            notes.append('Small sample: at least one period is under '+('200 plate appearances.' if discipline=='batting' else '50 innings.')+' Rates can move sharply.')
        if a and abs(b-a)/a>=.25:
            notes.append('Playing time differs by at least 25%; counting totals are not directly comparable.')
    if 'babip' in moves and abs(moves['babip'])>=.030:
        notes.append('BABIP moved by at least .030. Contact, defense and luck may all contribute; these stats do not identify the cause.')
    if len(evidence)<len(keys): notes.append('Some indicators are unavailable; the summary uses only the paired values shown.')
    return dict(title=title,kind=kind,evidence=evidence,notes=notes)

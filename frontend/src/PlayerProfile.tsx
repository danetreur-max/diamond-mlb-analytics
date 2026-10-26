import { useState } from 'react';
import SourceStats from './SourceStats';
type Row = Record<string, any>;
export const rate = (v: number | null | undefined, decimals = 3) => v == null ? '—' : decimals === 3 ? v.toFixed(decimals).replace(/^0\./, '.') : v.toFixed(decimals);
export const innings = (outs: number | undefined) => outs == null ? '—' : `${Math.floor(outs / 3)}.${outs % 3}`;
export function statCells(s: Row, pitching: boolean) {
  const t = s.totals, r = s.rates;
  return pitching ? [s.game_count, innings(t.outs_recorded), rate(r.era, 2), rate(r.whip, 2), t.strikeOuts ?? '—', r.k_pct == null ? '—' : `${(r.k_pct * 100).toFixed(1)}%`]
    : [s.game_count, t.plateAppearances ?? '—', t.homeRuns ?? '—', rate(r.avg), rate(r.obp), rate(r.slg), rate(r.ops)];
}
export const battingHeaders = ['G', 'PA', 'HR', 'AVG', 'OBP', 'SLG', 'OPS'];
export const pitchingHeaders = ['G', 'IP', 'ERA', 'WHIP', 'SO', 'K%'];
export default function PlayerProfile({ player, season, gameType, teams, onSelect, onBack, onGame, activeTab, onTabChange }: {
  player: Row; season: string; gameType: string; teams: Row[];
  onSelect: (season: string, gameType: string) => void; onBack: () => void; onGame: (id: number) => void;
  activeTab:string; onTabChange:(tab:string)=>void;
}) {
  const tab=activeTab, setTab=onTabChange;
  const [windowDays, setWindowDays] = useState('15');
  const teamName = (id: number) => teams.find(t => t.id === id)?.abbreviation || id;
  const sections = ['batting', 'pitching'].filter(group => player[group].game_count > 0);
  function table(rows: {label: string; data: Row}[], pitching: boolean) {
    return <div className="table-wrap"><table><thead><tr><th>Period</th>{(pitching ? pitchingHeaders : battingHeaders).map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.label}><td>{row.label}</td>{statCells(row.data, pitching).map((v, i) => <td key={i}>{v}</td>)}</tr>)}</tbody></table></div>;
  }
  return <section className="player-profile">
    <button className="text-button" onClick={onBack}>← Back to players</button>
    <div className="profile-heading"><div><div className="eyebrow">PLAYER PROFILE · MLB #{player.id}</div><h1>{player.full_name}</h1><p>{player.primary_position || 'Position unavailable'} · Bats {player.bat_side || '—'} / Throws {player.throw_hand || '—'}</p><div className="chips">{player.team_ids.map((id: number) => <span key={id}>{teamName(id)} · selected coverage</span>)}</div></div><div className="profile-filters"><label>Season<select value={season} onChange={e => onSelect(e.target.value, gameType)}><option value="">All seasons</option>{[...new Set([...(season ? [Number(season)] : []), ...player.seasons, ...(player.source_seasons || [])])].sort((a: any,b: any) => b-a).map((y: any) => <option key={y} value={y}>{y}</option>)}</select></label><label>Competition<select value={gameType} onChange={e => onSelect(season, e.target.value)}>{[['R','Regular season'],['S','Spring training'],['E','Exhibition'],['F','Wild Card'],['D','Division Series'],['L','Championship Series'],['W','World Series'],['A','All-Star']].map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label></div></div>
    <div className="coverage"><strong>Imported coverage</strong><span>{player.coverage_note} Latest player game: {player.as_of || 'none'}.</span></div>
    <div className="profile-tabs" role="tablist" aria-label="Player sections">{['Overview','Recent form','Splits','Season history','Game log','MLB summaries','Statcast'].map(name => <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? 'selected' : ''} onClick={() => setTab(name)}>{name}</button>)}</div>
    {(tab==='MLB summaries'||tab==='Statcast')&&<SourceStats playerId={player.id} season={season} gameType={gameType} statcast={tab==='Statcast'}/>}
    {!sections.length && !['MLB summaries','Statcast'].includes(tab) && <div className="panel empty">No imported appearances for this season and competition. Choose another selection.</div>}
    {tab === 'Recent form' && <div className="filters"><label>Calendar window <select value={windowDays} onChange={e => setWindowDays(e.target.value)}>{['7','15','30'].map(d => <option key={d} value={d}>Last {d} days</option>)}</select></label><p>Window ends at the latest imported player game ({player.as_of || '—'}), not today.</p></div>}
    {(['MLB summaries','Statcast'].includes(tab) ? [] : tab === 'Season history' ? ['batting','pitching'].filter(g => player.career.some((s: Row) => s[g].game_count > 0)) : sections).map(group => {
      const pitching = group === 'pitching', data = player[group];
      const selected = tab === 'Recent form' ? data.recent[windowDays] : data;
      return <section className="panel" key={group}><div className="section-head"><div><h2>{pitching ? 'Pitching' : 'Batting'}</h2><span>{tab === 'Season history' ? 'Imported season totals · selected competition' : `${selected?.game_count ?? 0} imported appearances · ${selected?.first_date || '—'} to ${selected?.last_date || '—'}`}</span></div></div>
        {(tab === 'Overview' || tab === 'Recent form') && <div className="player-stat-grid">{(pitching ? pitchingHeaders : battingHeaders).map((label, i) => <div key={label}><span title={label === 'IP' ? 'Innings pitched: .1 is one out, .2 is two outs' : undefined}>{label}</span><strong>{statCells(selected, pitching)[i]}</strong></div>)}</div>}
        {tab === 'Overview' && <p className="profile-note">{pitching ? 'ERA and WHIP use total recorded outs. K% uses batters faced.' : 'Rate statistics are calculated from combined counting totals.'} Missing denominators display as —.</p>}
        {tab === 'Splits' && table([{label:'Home',data:data.splits.home},{label:'Away',data:data.splits.away}],pitching)}
        {tab === 'Season history' && table(player.career.filter((s: Row) => s[group].game_count).map((s: Row) => ({label:String(s.season),data:s[group]})),pitching)}
        {tab === 'Game log' && <div className="table-wrap"><table><thead><tr><th>Date / game</th><th>Team</th><th>Opponent</th>{(pitching ? ['IP','H','ER','BB','SO'] : ['AB','H','HR','RBI','BB','SO']).map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{data.games.map((r: Row) => <tr key={r.game_id}><td><button className="text-button" onClick={() => onGame(r.game_id)}>{r.date}</button></td><td>{teamName(r.team_id)}</td><td>{r.venue === 'home' ? 'vs' : '@'} {teamName(r.opponent_id)}</td>{(pitching ? [innings(r.outs_recorded),r.stats.hits,r.stats.earnedRuns,r.stats.baseOnBalls,r.stats.strikeOuts] : [r.stats.atBats,r.stats.hits,r.stats.homeRuns,r.stats.rbi,r.stats.baseOnBalls,r.stats.strikeOuts]).map((v,i) => <td key={i}>{v ?? '—'}</td>)}</tr>)}</tbody></table></div>}
      </section>;
    })}
  </section>;
}

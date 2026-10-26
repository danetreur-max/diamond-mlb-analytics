import { useEffect, useState } from 'react';
type Row = Record<string, any>;
const localDate = (value:string) => new Date(value).toLocaleDateString('en-US', {timeZone:'America/Los_Angeles'});
const formatStatcast = (key:string, value:string) => {
  if(value==null||value==='')return '—';
  const numeric=Number(value);
  if(!Number.isFinite(numeric))return value;
  if(['ba','est_ba','slg','est_slg','woba','est_woba'].includes(key))return numeric.toFixed(3).replace(/^0\./,'.');
  if(['avg_hit_speed','max_hit_speed','ev95percent','brl_percent','pitch_usage','whiff_percent'].includes(key))return numeric.toFixed(1);
  return Number.isInteger(numeric)?numeric.toLocaleString():value;
};
export default function SourceStats({playerId, season, gameType, statcast}: {playerId:number;season:string;gameType:string;statcast:boolean}) {
  const [data,setData] = useState<Row | null>(null), [error,setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setData(null);setError('');
    const refresh = () => fetch(`/api/players/${playerId}/source-stats`,{signal:controller.signal})
      .then(async r => {if(!r.ok)throw new Error('Source summaries are temporarily unavailable.');return r.json();})
      .then(value=>{setData(value);setError('');}).catch(e => {if(e.name!=='AbortError')setError(e.message);});
    refresh();
    const timer=window.setInterval(refresh,15000);
    return () => {controller.abort();window.clearInterval(timer);};
  },[playerId,statcast]);
  if(error)return <div className="panel empty" role="alert">{error}</div>;
  if(!data)return <div className="panel empty">Loading source summaries…</div>;
  const competition = gameType === 'R' ? 'R' : ['F','D','L','W'].includes(gameType) ? 'P' : null;
  if(statcast) {
    if(gameType!=='R')return <div className="panel empty">Statcast season leaderboards cover the regular season. Select Regular season to view them.</div>;
    const rows = data.statcast.filter((r:Row) => !season || r.season === Number(season));
    if(!rows.length)return <div className="panel empty">No Statcast summaries have been ingested for this selection yet.</div>;
    return <>{['expected_statistics','statcast','pitch-arsenal-stats'].map(dataset => {
      const selected=rows.filter((r:Row)=>r.dataset===dataset);
      if(!selected.length)return null;
      const columns = dataset==='expected_statistics' ? [['pa','PA'],['bip','BIP'],['ba','AVG'],['est_ba','xBA'],['slg','SLG'],['est_slg','xSLG'],['woba','wOBA'],['est_woba','xwOBA']]
        : dataset==='statcast' ? [['attempts','Batted balls'],['avg_hit_speed','Avg EV (mph)'],['max_hit_speed','Max EV (mph)'],['ev95percent','Hard-hit %'],['barrels','Barrels'],['brl_percent','Barrel %']]
        : [['pitch_name','Pitch'],['pitches','Pitches'],['pitch_usage','Usage %'],['whiff_percent','Whiff %'],['ba','AVG'],['est_ba','xBA'],['woba','wOBA'],['est_woba','xwOBA']];
      return <section className="panel" key={dataset}><div className="section-head"><div><h2>{dataset==='expected_statistics'?'Expected outcomes':dataset==='statcast'?'Contact quality':'Pitch arsenal'}</h2><span>Baseball Savant · regular-season summaries · retrieved {localDate(selected[0].fetched_at)}</span></div></div><div className="table-wrap"><table><thead><tr><th>Season</th><th>Role / team</th>{columns.map(([key,label])=><th key={key}>{label}</th>)}</tr></thead><tbody>{selected.map((r:Row,i:number)=><tr key={i}><td>{r.season}</td><td>{r.role==='batter'?'Hitting':'Pitching'}{r.stats.team_name_alt ? ` · ${r.stats.team_name_alt}`:''}</td>{columns.map(([key])=><td key={key}>{formatStatcast(key,r.stats[key])}</td>)}</tr>)}</tbody></table></div></section>;
    })}</>;
  }
  if(!competition)return <div className="panel empty">MLB source summaries cover regular season and combined postseason. Select one of those competitions.</div>;
  const rows=data.rows.filter((r:Row)=>r.competition===competition && (r.scope==='career' || !season || r.season===Number(season)));
  if(!rows.length)return <div className="panel empty">No MLB source summaries have been ingested for this selection yet.</div>;
  return <><div className="coverage"><strong>MLB source summaries</strong><span>{competition==='P'?'Combined postseason, including all rounds.':'Regular season.'} These totals are independent of local game imports. Career rows cover all available seasons as of retrieval. Team rows and total rows are separate.</span></div>{['hitting','pitching','fielding'].map(group=>{
    const selected=rows.filter((r:Row)=>r.discipline===group);
    if(!selected.length)return null;
    const columns=group==='hitting'?[['gamesPlayed','G'],['plateAppearances','PA'],['homeRuns','HR'],['avg','AVG'],['obp','OBP'],['slg','SLG'],['ops','OPS']]:group==='pitching'?[['gamesPlayed','G'],['inningsPitched','IP'],['era','ERA'],['whip','WHIP'],['strikeOuts','SO']]:[['gamesPlayed','G'],['assists','A'],['putOuts','PO'],['errors','E'],['fielding','FLD%']];
    return <section className="panel" key={group}><div className="section-head"><div><h2>{group[0].toUpperCase()+group.slice(1)}</h2><span>MLB Stats API · retrieved {localDate(selected[0].fetched_at)}</span></div></div><div className="table-wrap"><table><thead><tr><th>Period</th><th>Team / scope</th>{group==='fielding'&&<th>Position</th>}{columns.map(([key,label])=><th key={key}>{label}</th>)}</tr></thead><tbody>{selected.map((r:Row)=><tr key={r.key}><td>{r.scope==='career'?'Career':r.season}</td><td>{r.metadata_json.team?.name || 'All teams'}</td>{group==='fielding'&&<td>{r.position||r.stats.position?.abbreviation||'—'}</td>}{columns.map(([key])=><td key={key}>{r.stats[key] ?? '—'}</td>)}</tr>)}</tbody></table></div></section>;
  })}</>;
}

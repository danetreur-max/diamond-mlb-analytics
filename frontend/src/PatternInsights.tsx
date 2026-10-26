import React,{useEffect,useRef,useState} from 'react';
import {useApiData} from './Reliability';
import {advancedHelp,ChangeBadge} from './Refinements';
import './patterns.css';
type Row=Record<string,any>;
const labels:Record<string,string>={ops:'OPS',iso:'ISO',babip:'BABIP',kPct:'Strikeout %',bbPct:'Walk %',kMinusBbPct:'K − BB %',era:'ERA',whip:'WHIP',hr9:'HR/9'};
const pct=(key:string)=>['kPct','bbPct','kMinusBbPct'].includes(key);
const value=(key:string,n:any)=>n==null?'—':Number(n).toFixed(['ops','iso','babip','est_woba','woba','est_ba','ba','est_slg','slg'].includes(key)?3:2);
const difference=(key:string,n:number)=>(n>0?'+':'')+value(key,n)+(pct(key)?' pp':'');
export function PatternEvidence({pattern,discipline}:{pattern?:Row;discipline:string}){
  if(!pattern)return <span>Pattern unavailable</span>;
  return <div className="pattern-summary"><strong className={'pattern-label '+pattern.kind}>{pattern.kind==='mixed'?'↔ ':pattern.kind==='aligned'?'↗ ':''}{pattern.title}</strong><details><summary>Evidence & context</summary><p>Current period minus previous period. “Similar” means below the stated threshold, not statistically equivalent.</p><ul>{pattern.evidence.map((r:Row)=><li key={r.metric} title={advancedHelp[r.metric]}><strong>{labels[r.metric]||r.metric}</strong>: {value(r.metric,r.before)} → {value(r.metric,r.after)} ({difference(r.metric,r.delta)}). Pattern threshold: {value(r.metric,r.threshold)}{pct(r.metric)?' percentage points':''}.</li>)}</ul>{pattern.notes.map((n:string)=><p className="sample-warning" key={n}>{n}</p>)}<p>Fixed screening thresholds, not significance tests. No injury, luck, or mechanical cause is inferred.</p></details></div>;
}
export function statcastPairs(rows:Row[],season:number,discipline:string){
  const role=discipline==='batting'?'batter':'pitcher';
  const definitions=[['expected_statistics','est_ba','Expected batting average'],['expected_statistics','est_slg','Expected slugging'],['expected_statistics','est_woba','Expected wOBA'],['statcast','avg_hit_speed','Average exit velocity (mph)'],['statcast','ev95percent','Hard-hit %'],['statcast','brl_percent','Barrel %']];
  return definitions.map(([dataset,metric,label])=>{
    const pair=[season-1,season].map(year=>rows.find(r=>r.role===role&&Number(r.season)===year&&r.dataset===dataset));
    const numbers=pair.map(r=>{const raw=r?.stats?.[metric];return raw!=null&&raw!==''&&Number.isFinite(Number(raw))?Number(raw):null;});
    return {metric,label,before:numbers[0],after:numbers[1],stamps:pair.map(r=>r?.fetched_at),delta:numbers[0]!=null&&numbers[1]!=null?numbers[1]-numbers[0]:null};
  });
}
function StatcastEvidence({id,season,gameType,discipline,pattern}:{id:number;season:number;gameType:string;discipline:string;pattern:Row}){
  const state=useApiData(gameType==='R'?`/players/${id}/source-stats`:null);
  const rows=statcastPairs(state.data?.statcast||[],season,discipline);
  const expected=rows.find(r=>r.metric==='est_woba');
  const result=pattern.evidence.find((r:Row)=>r.metric===(discipline==='batting'?'ops':'era'));
  const conflict=expected?.delta!=null&&result&&(discipline==='batting'?result.delta>=.05&&expected.delta<=-.020:result.delta<=-.5&&expected.delta>=.020);
  return <section><h3>Does contact quality tell the same story?</h3>{gameType!=='R'?<p>Statcast season summaries cover the regular season only.</p>:<><p>Batters: contact made. Pitchers: contact allowed. Expected statistics describe batted-ball characteristics; they do not predict future performance.</p>{state.error&&<p className="error">{state.error}<button onClick={state.retry}>Retry</button></p>}{state.loading&&!state.data&&<p role="status">Loading Statcast evidence…</p>}{state.data&&<><div className="table-wrap"><table><thead><tr><th>Indicator</th><th>{season-1}</th><th>{season}</th><th>Change</th></tr></thead><tbody>{rows.map(r=><tr key={r.metric}><th>{r.label}</th><td>{value(r.metric,r.before)}</td><td>{value(r.metric,r.after)}</td><td>{r.delta==null?'—':difference(r.metric,r.delta)}</td></tr>)}</tbody></table></div>{conflict&&<p className="sample-warning">Results and expected contact outcomes moved in different directions: {discipline==='batting'?'OPS rose while expected wOBA fell':'ERA fell while expected wOBA allowed rose'}. This is a reason to investigate, not evidence of a cause.</p>}<p className="source-note">Baseball Savant imported season summaries. — means one or both years lack that indicator. Conflict flags require an OPS rise of .050 (or ERA drop of .50), plus an opposite expected wOBA move of .020.</p><p className="source-note">Retrieved: {[...new Set(rows.flatMap(r=>r.stamps).filter(Boolean))].map(stamp=>new Date(stamp).toLocaleString()).join(' · ')||'No matching snapshots available'}.</p></>}</>}</section>;
}
export function PlayerChangeDetails({player,season,gameType,discipline,onClose}:{player:Row;season:number;gameType:string;discipline:string;onClose:()=>void}){
  const root=useRef<HTMLElement>(null),[appearances,setAppearances]=useState(30),[enabled,setEnabled]=useState(false);
  const period=useApiData(enabled?`/workspace/players/${player.id}/period-changes?season=${season}&game_type=${gameType}&discipline=${discipline}&appearances=${appearances}`:null);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;root.current?.focus({preventScroll:true});const close=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();onClose();}};document.addEventListener('keydown',close);return()=>{document.removeEventListener('keydown',close);if(previous?.isConnected)previous.focus({preventScroll:true});};},[]);
  return <aside ref={root} tabIndex={-1} className="change-explorer" aria-label={'Change breakdown for '+player.name}><div className="section-head"><div><h2>What changed for {player.name}?</h2><span>{season-1} → {season} · {discipline}</span></div><button className="secondary" onClick={onClose}>Close breakdown</button></div><PatternEvidence pattern={player.pattern} discipline={discipline}/><StatcastEvidence id={player.id} season={season} gameType={gameType} discipline={discipline} pattern={player.pattern}/><section><h3>Compare equivalent stretches</h3><p>Compare the first N complete {discipline==='batting'?'batting':'pitching'} appearances in each season. Workload can still differ within those appearances. This view requires complete imported coverage in both years.</p><div className="quick-actions"><label>Appearances per year <select value={appearances} onChange={e=>setAppearances(Number(e.target.value))}>{[5,10,20,30,50,100].map(n=><option key={n}>{n}</option>)}</select></label><button className="secondary" onClick={()=>{setEnabled(true);if(enabled)period.retry();}}>Compare first {appearances} appearances</button></div>{period.loading&&enabled&&!period.data&&<p role="status">Comparing imported appearances…</p>}{period.error&&<p className="error">{period.error}<button onClick={period.retry}>Retry</button></p>}{period.data&&(period.data.available?<><p>{period.data.periods.map((p:Row)=>`${p.season}: ${p.start} to ${p.end}; ${discipline==='batting'?p.stats.plateAppearances+' PA':`${Math.floor(p.stats.outs_recorded/3)}.${p.stats.outs_recorded%3} IP`}`).join(' · ')}</p><PatternEvidence pattern={period.data.pattern} discipline={discipline}/><p className="source-note">{period.data.note}{period.data.stale?' Schedule audit is cached; recheck after refreshing.':''}</p></>:<p className="sample-warning">{period.data.reason}</p>)}</section><button className="text-button" onClick={()=>location.hash='/players/'+player.id}>Open full player profile →</button></aside>;
}
export function savedRateChanges(before:Row,after:Row){
  const limits:Record<string,number>={ops:.030,iso:.020,kPct:1,bbPct:1,era:.30,whip:.08,kMinusBbPct:2};
  return ['batting','pitching'].flatMap(discipline=>Object.entries(limits).flatMap(([metric,threshold])=>{const a=before[discipline]?.[metric],b=after[discipline]?.[metric];return typeof a==='number'&&typeof b==='number'&&Math.abs(b-a)>=threshold-1e-9?[{discipline,metric,before:a,after:b}]:[];}));
}
function SavedPlayerUpdate({player,season,gameType}:{player:Row;season:number;gameType:string}){
  const key=`diamond:review:${player.id}:${season}:${gameType}`;
  const [baseline,setBaseline]=useState<Row|null>(()=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}});
  const state=useApiData(`/explore/players/${player.id}?season=${season}&game_type=${gameType}`);
  const record=()=>{if(!state.data||state.data.stale)return;const next={stats:state.data.season_stats,at:new Date().toISOString()};try{localStorage.setItem(key,JSON.stringify(next));setBaseline(next);}catch{}};
  useEffect(()=>{if(state.data&&!baseline&&!state.data.stale)record();},[state.data]);
  const changes=baseline&&state.data?savedRateChanges(baseline.stats,state.data.season_stats):[];
  return <article className="saved-update"><button className="text-button" onClick={()=>location.hash='/players/'+player.id}>{player.name}</button>{state.error&&<p className="error">{state.error}</p>}{!baseline?<p>{state.loading?'Loading…':'A baseline will be saved when fresh data is available.'}</p>:<><small>Reviewed {new Date(baseline.at).toLocaleString()}</small>{changes.length?<ul>{changes.map(r=><li key={r.discipline+r.metric}>{r.discipline} {labels[r.metric]}: {value(r.metric,r.before)} → {value(r.metric,r.after)} <ChangeBadge {...r}/></li>)}</ul>:<p>No changes above the screening thresholds since review.</p>}<button className="secondary" disabled={!state.data||state.data.stale} onClick={record}>Mark current stats reviewed</button>{state.data?.stale&&<p>Cached results; review baseline is preserved.</p>}</>}</article>;
}
export function SavedUpdates({players,season,gameType}:{players:Row[];season:number;gameType:string}){
  if(!players.length)return null;
  return <section className="panel"><h2>Changes since your last review</h2><p>Saved players only · {season} · {gameType}. Baselines stay in this browser and are specific to player, year and competition. Your first visit establishes a baseline; use “Mark current stats reviewed” to reset it.</p><details><summary>Screening thresholds</summary><p>OPS .030; ISO .020; strikeout or walk rate 1 percentage point; ERA .30; WHIP .08; K−BB% 2 percentage points. Descriptive changes, not significance tests.</p></details><div className="favorite-grid">{players.map(p=><SavedPlayerUpdate key={`${p.id}:${season}:${gameType}`} player={p} season={season} gameType={gameType}/>)}</div></section>;
}


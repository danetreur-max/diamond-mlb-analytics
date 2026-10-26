const fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript'),assert=require('node:assert/strict');
function compile(file,extras={}){const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;const exports={};vm.runInNewContext(code,{exports,Map,AbortSignal,Error,console,require:()=>({createElement:(tag,props,...children)=>({tag,props,children}),useEffect(){},useState(){}}),...extras});return exports;}
const ui=compile('src/Refinements.tsx');
const text=node=>node==null?'':typeof node==='object'?(node.children||[]).map(text).join(' '):String(node);
assert.match(text(ui.ChangeBadge({metric:'era',before:4,after:3})),/↓ Improved/);
assert.match(text(ui.ChangeBadge({metric:'ops',before:.7,after:.8})),/↑ Improved/);
assert.match(text(ui.ChangeBadge({metric:'whip',before:1,after:1.2})),/↑ Declined/);
assert.match(text(ui.ChangeBadge({metric:'ops',before:.8,after:.8})),/No change/);
assert.doesNotMatch(text(ui.ChangeBadge({metric:'era',before:0,after:1,percent:true})),/Infinity|NaN/);
assert.match(text(ui.ChangeBadge({metric:'kPct',discipline:'batting',before:20,after:15})),/↓ Improved/);
assert.match(text(ui.ChangeBadge({metric:'bbPct',discipline:'pitching',before:10,after:12})),/↑ Declined/);
assert.match(text(ui.ChangeBadge({metric:'bb9',before:4,after:3})),/↓ Improved/);
assert.match(text(ui.ChangeBadge({metric:'homeRuns',before:20,after:30})),/↑ Increased/);
assert.match(text(ui.ChangeBadge({metric:'babip',before:.3,after:.35})),/↑ Increased/);
assert.deepEqual(Array.from(ui.presetColumns.batting.beginner),['gamesPlayed','hits','homeRuns','avg','obp']);
assert.deepEqual(Array.from(ui.presetColumns.pitching.beginner),['outs_recorded','strikeOuts','era','whip']);
(async()=>{
  let mode='offline';
  const fetch=async path=>{if(mode==='offline')throw new Error('Failed to fetch');if(path==='/api/health')return {ok:false};if(mode==='proxy')return {ok:false,json:async()=>{throw new Error('Not JSON')}};if(mode==='mlb')return {ok:false,json:async()=>({detail:'MLB data is temporarily unavailable. Please retry.'})};return {ok:true,json:async()=>({items:[]})};};
  const {request}=compile('src/Reliability.ts',{fetch});
  await assert.rejects(()=>request('/players'),/data service cannot be reached/);
  mode='proxy';await assert.rejects(()=>request('/players'),/data service is unavailable/);
  mode='mlb';await assert.rejects(()=>request('/players'),/MLB data is temporarily unavailable/);
  mode='ok';assert.deepEqual(Array.from((await request('/players')).items),[]);
  console.log('Refinement checks passed: change direction, neutral/zero baselines, beginner presets, offline/proxy/upstream errors and successful data.');
})().catch(e=>{console.error(e);process.exitCode=1;});

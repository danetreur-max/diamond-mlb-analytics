type Row=Record<string,any>;
export const patternFilters=[['all','All patterns'],['power','More power'],['mixed','Mixed signals'],['pitching','Better pitching indicators'],['workload','Large workload change']] as const;
export function matchesPattern(row:Row,filter:string,discipline:string){
  const evidence:Row[]=row.pattern?.evidence||[];
  const moved=(metric:string,minimum:number)=>evidence.some(r=>r.metric===metric&&typeof r.delta==='number'&&(minimum>0?r.delta>=minimum-1e-9:r.delta<=minimum+1e-9));
  switch(filter){
    case 'power':return discipline==='batting'&&moved('iso',.030);
    case 'mixed':return row.pattern?.kind==='mixed';
    case 'pitching':return discipline==='pitching'&&(moved('era',-.50)||moved('kMinusBbPct',3)||moved('whip',-.10));
    case 'workload':return typeof row.before_sample==='number'&&row.before_sample>0&&typeof row.after_sample==='number'&&Math.abs(row.after_sample-row.before_sample)/row.before_sample>=.25-1e-9;
    default:return true;
  }
}

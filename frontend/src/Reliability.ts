import {useEffect,useState} from 'react';
const cache=new Map<string,any>();
export async function request(path:string,init?:RequestInit){
  let response:Response;
  try{response=await fetch('/api'+path,init);}catch(e){if((e as Error).name==='AbortError')throw e;throw new Error('The data service cannot be reached. Please retry shortly.');}
  const body=await response.json().catch(()=>null);
  if(!response.ok){
    if(body?.detail){throw new Error(typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map((x:any)=>typeof x==='string'?x:x.msg?.replace(/^Value error, /,'')).join(' '):'The data server encountered an error. Please retry.');}
    let healthy=false;try{const health=await fetch('/api/health',{signal:AbortSignal.timeout(2500)});healthy=health.ok;}catch{}
    throw new Error(healthy?'The data server encountered an error. Please retry.':'The data service is unavailable. Please retry shortly.');
  }
  return body;
}
export function useApiData(path:string|null,revision=0){
  const [attempt,setAttempt]=useState(0);
  const [state,setState]=useState<{path:string|null;data:any;error:string;loading:boolean}>({path,data:path?cache.get(path)??null:null,error:'',loading:Boolean(path)});
  useEffect(()=>{const abort=new AbortController();if(!path){setState({path,data:null,error:'',loading:false});return;}setState({path,data:cache.get(path)??null,error:'',loading:true});request(path,{signal:abort.signal}).then(data=>{if(abort.signal.aborted)return;cache.delete(path);cache.set(path,data);if(cache.size>40)cache.delete(cache.keys().next().value!);setState({path,data,error:'',loading:false});}).catch(e=>{if(!abort.signal.aborted){const data=cache.get(path)??null;setState({path,data,error:e.message+(data?' Showing the last loaded results for this selection.':''),loading:false});}});return()=>abort.abort();},[path,revision,attempt]);
  return {...(state.path===path?state:{path,data:path?cache.get(path)??null:null,error:'',loading:Boolean(path)}),retry:()=>setAttempt(v=>v+1)};
}

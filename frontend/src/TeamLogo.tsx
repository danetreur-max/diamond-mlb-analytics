import React,{useState,useEffect} from 'react';
import './team-logos.css';
export default function TeamLogo({id,name,abbreviation='',large=false}:{id:number;name:string;abbreviation?:string;large?:boolean}){
 const [failed,setFailed]=useState(false);useEffect(()=>setFailed(false),[id]);
 return <span className={'mlb-team-logo'+(large?' large':'')} title={name}>{failed?<span aria-label={name}>{abbreviation||name.slice(0,3)}</span>:<img src={`/team-logos/${id}.svg`} alt={`${name} logo`} width={large?84:32} height={large?84:32} loading={large?'eager':'lazy'} onError={()=>setFailed(true)}/>}</span>;
}

#!/usr/bin/env python3
import json, os, datetime
BASE=os.path.dirname(os.path.abspath(__file__))
data=json.load(open(os.path.join(BASE,'data_all.json'),encoding='utf-8'))
for r in data: r.setdefault('continent','Europa')
built=datetime.date.today().isoformat()
nuni=len(set(r['university'] for r in data))
payload=json.dumps(data, ensure_ascii=False)

html=r'''<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forhåndsgodkjente utvekslingsemner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --primary:#800020;--primary-dark:#4a0012;--primary-tint:#f7eef0;--primary-line:#ecd9de;
  --bg:#faf9f6;--card:#ffffff;--fg:#212b36;--mut:#637381;--line:#dfe3e8;
  --ok:#229a52;--no:#d33a2f;--chip:#f4f6f8;
  --font:'Public Sans',-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;font-family:var(--font);color:var(--fg);line-height:1.5;background:var(--bg)}
header{padding:30px 20px 6px;max-width:1080px;margin:0 auto}
h1{font-size:24px;font-weight:700;margin:0;letter-spacing:-.01em}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 60px}
.searchbar{position:sticky;top:0;padding:14px 0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line)}
.searchrow{position:relative}
input[type=text]{width:100%;padding:14px 54px 14px 16px;font-size:17px;font-family:var(--font);border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
input[type=text]:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-tint)}
.filterbtn{position:absolute;top:50%;right:8px;transform:translateY(-50%);width:38px;height:38px;border:none;border-radius:8px;background:transparent;color:var(--mut);font-size:22px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center}
.filterbtn:hover{background:var(--chip);color:var(--primary)}
.filterbtn.active{background:var(--primary);color:#fff}
.panel{display:none;margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;box-shadow:0 8px 24px rgba(33,43,54,.10)}
.panel.open{display:block}
.panel .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.field label{display:block;font-size:12px;font-weight:600;color:var(--mut);margin-bottom:4px;text-transform:uppercase;letter-spacing:.03em}
select{width:100%;padding:10px 12px;font-size:14px;font-family:var(--font);border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
select:focus{outline:none;border-color:var(--primary)}
.panel .foot{display:flex;justify-content:flex-end;margin-top:12px}
.reset{padding:9px 14px;font-size:13px;font-weight:600;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--mut);cursor:pointer}
.reset:hover{color:var(--primary);border-color:var(--primary)}
.count{font-size:14px;color:var(--mut);margin:16px 2px}
.uni{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;overflow:hidden}
.uni h2{font-size:16px;font-weight:600;margin:0;padding:13px 16px;background:#fbfaf8;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.uni h2 .c{color:var(--mut);font-weight:400;font-size:13px}
.row{padding:14px 16px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.row.rej{opacity:.5}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.host{font-weight:600;font-size:15px}
.badge{font-size:11px;font-weight:600;padding:2px 9px;border-radius:20px;background:var(--primary-tint);color:var(--primary);border:1px solid var(--primary-line);white-space:nowrap}
.g-ok{color:var(--ok);font-weight:600}
.g-no{color:var(--no);font-weight:600}
.meta{margin-top:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:6px 18px;font-size:13.5px}
.meta div span{color:var(--mut)}
.cmt{margin-top:8px;font-size:13px;color:var(--mut);font-style:italic}
a{color:var(--primary);text-decoration:none}a:hover{text-decoration:underline}
.empty{color:var(--mut);text-align:center;padding:50px 0}
mark{background:#f3e2c6;color:inherit;padding:0 2px;border-radius:3px}
.foot{color:var(--mut);font-size:12px;margin-top:30px;text-align:center}
</style>
</head>
<body>
<header><h1>Indøk utvekslingsemner</h1></header>
<div class="wrap">
  <div class="searchbar">
    <div class="searchrow">
      <input id="q" type="text" placeholder="Søk NTNU-emnekode, f.eks. TIØ4162…" autocomplete="off" autofocus>
      <button class="filterbtn" id="filterbtn" title="Filtrer" aria-label="Filtrer">&#7140;</button>
    </div>
    <div class="panel" id="panel">
      <div class="grid">
        <div class="field"><label>Verdensdel</label><select id="fCont"></select></div>
        <div class="field"><label>Land</label><select id="fCountry"></select></div>
        <div class="field"><label>Universitet</label><select id="fUni"></select></div>
      </div>
      <div class="foot"><button class="reset" id="reset">Nullstill filtre</button></div>
    </div>
  </div>
  <div class="count" id="count"></div>
  <div id="results"></div>
  <div class="foot">Kilde: NTNU Wikihotell – rom «Utland» · __C__ · __U__ universiteter · __N__ registrerte vurderinger · bygget __DATE__.<br>
  Uoffisiell oversikt. Sjekk alltid mot den offisielle wiki-siden før du søker forhåndsgodkjenning.</div>
</div>
<script>
const DATA = __PAYLOAD__;
const q=document.getElementById('q'), results=document.getElementById('results'), count=document.getElementById('count');
const fCont=document.getElementById('fCont'), fCountry=document.getElementById('fCountry'), fUni=document.getElementById('fUni');
const panel=document.getElementById('panel'), filterbtn=document.getElementById('filterbtn');

filterbtn.onclick=()=>{panel.classList.toggle('open');filterbtn.classList.toggle('active',panel.classList.contains('open'));};

function opts(sel, values, allLabel){
  const cur=sel.value;
  sel.innerHTML='<option value="">'+allLabel+'</option>'+values.map(v=>`<option>${v}</option>`).join('');
  if(values.includes(cur)) sel.value=cur;
}
function uniq(arr){return [...new Set(arr)].sort((a,b)=>a.localeCompare(b,'no'));}
opts(fCont, uniq(DATA.map(r=>r.continent)), 'Alle verdensdeler');

function refreshDropdowns(){
  const cont=fCont.value;
  const inCont=DATA.filter(r=>!cont||r.continent===cont);
  opts(fCountry, uniq(inCont.map(r=>r.country)), 'Alle land');
  const country=fCountry.value;
  const inCountry=inCont.filter(r=>!country||r.country===country);
  opts(fUni, uniq(inCountry.map(r=>r.university)), 'Alle universiteter');
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function hl(s,term){s=esc(s);if(!term)return s;try{return s.replace(new RegExp('('+term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>');}catch(e){return s;}}
function gclass(g){const t=(g||'').toLowerCase();if(t.startsWith('ja')||t==='ok'||t.startsWith('godkj'))return 'g-ok';if(t.startsWith('nei')||t.startsWith('no'))return 'g-no';return '';}

function updateBtn(){
  const active=fCont.value||fCountry.value||fUni.value;
  filterbtn.classList.toggle('active', !!active || panel.classList.contains('open'));
}
function run(){
  const raw=q.value.trim();
  const term=raw.toUpperCase().replace(/\s+/g,'');
  const cont=fCont.value, country=fCountry.value, uni=fUni.value;
  let base=DATA.filter(r=>(!cont||r.continent===cont)&&(!country||r.country===country)&&(!uni||r.university===uni));
  let hits=base;
  if(raw){
    hits=base.filter(r=>
      r.ntnu_codes.some(c=>c.includes(term)) ||
      r.ntnu_emnekode.toUpperCase().replace(/\s+/g,'').includes(term)
    );
  }
  updateBtn();
  const filtering = raw||cont||country||uni;
  if(!filtering){results.innerHTML='<div class="empty">Søk på en NTNU-emnekode, eller bruk filteret (&#7140;) for å bla etter land og universitet.</div>';count.textContent='';return;}
  const groups={};
  hits.forEach(r=>{(groups[r.university]=groups[r.university]||{country:r.country,continent:r.continent,url:r.url,rows:[]}).rows.push(r);});
  const unis=Object.keys(groups).sort((a,b)=>a.localeCompare(b,'no'));
  count.textContent = hits.length ? `${hits.length} treff hos ${unis.length} universitet${unis.length>1?'er':''}` : '';
  if(!hits.length){results.innerHTML='<div class="empty">Ingen treff. Justér søk eller filtre (bruk Ø, ikke O, i fagkoder).</div>';return;}
  results.innerHTML=unis.map(u=>{
    const g=groups[u];
    const rows=g.rows.map(r=>{
      const meta=[
        ['Emnekode ('+esc(u)+')',r.fagkode_host],
        ['Studiepoeng',r.studiepoeng],
        ['NTNU-emne',r.ntnu_emnekode+(r.ntnu_emnenavn?' – '+r.ntnu_emnenavn:'')],
        ['Emnetype',r.emnetype],
        ['Behandlingsdato',r.behandlingsdato],
        ['Behandlet av',r.behandlet_av],
      ].filter(m=>m[1]&&m[1].trim());
      return `<div class="row ${r.rejected?'rej':''}">
        <div class="top">
          <span class="host">${hl(r.fagnavn_host||r.fagkode_host||'(uten navn)',raw)}</span>
          ${r.ntnu_codes.map(c=>`<span class="badge">${esc(c)}</span>`).join('')}
          <span class="${gclass(r.godkjent)}">${esc(r.godkjent||'')}${r.rejected?' (strøket)':''}</span>
        </div>
        <div class="meta">${meta.map(m=>`<div><span>${m[0]}:</span> ${hl(m[1],raw)}</div>`).join('')}</div>
        ${r.kommentar?`<div class="cmt">${hl(r.kommentar,raw)}</div>`:''}
      </div>`;
    }).join('');
    return `<div class="uni"><h2><span>${esc(u)}</span><span class="c">${esc(g.country)} · ${esc(g.continent)} · <a href="${g.url}" target="_blank" rel="noopener">åpne wiki-side ↗</a></span></h2>${rows}</div>`;
  }).join('');
}

fCont.addEventListener('change',()=>{refreshDropdowns();run();});
fCountry.addEventListener('change',()=>{refreshDropdowns();run();});
fUni.addEventListener('change',run);
q.addEventListener('input',run);
document.getElementById('reset').onclick=()=>{fCont.value='';refreshDropdowns();run();};

refreshDropdowns();
run();
</script>
</body>
</html>'''

html=html.replace('__PAYLOAD__',payload).replace('__DATE__',built).replace('__N__',str(len(data))).replace('__U__',str(nuni)).replace('__C__', ' · '.join(dict.fromkeys(r['continent'] for r in data)))
out=os.path.join(BASE,'indok-utveksling.html')
open(out,'w',encoding='utf-8').write(html)
print('wrote',out,round(os.path.getsize(out)/1024),'KB')

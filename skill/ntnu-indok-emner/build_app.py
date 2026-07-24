#!/usr/bin/env python3
import json, os, datetime
BASE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.abspath(os.path.join(BASE,'..','..'))
data=json.load(open(os.path.join(ROOT,'data','data_all.json'),encoding='utf-8'))
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
  color-scheme: light dark;
  --primary:#800020;--primary-dark:#4a0012;--primary-tint:#f7eef0;--primary-line:#ecd9de;
  --bg:#faf9f6;--card:#ffffff;--fg:#212b36;--mut:#637381;--line:#dfe3e8;
  --ok:#229a52;--no:#d33a2f;--chip:#f4f6f8;
  --font:'Public Sans',-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
@media (prefers-color-scheme: dark){
  :root{
    --primary:#e2879c;--primary-dark:#f0aebd;--primary-tint:#3a232a;--primary-line:#5c333d;
    --bg:#14171a;--card:#1c2024;--fg:#e7e9ec;--mut:#9aa4ae;--line:#2b3138;
    --ok:#57cf8e;--no:#f2776c;--chip:#242a30}
}
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
.checks{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}
.checkfield{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--fg);cursor:pointer}
.panel .foot{display:flex;justify-content:flex-end;margin-top:12px}
.reset{padding:9px 14px;font-size:13px;font-weight:600;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--mut);cursor:pointer}
.reset:hover{color:var(--primary);border-color:var(--primary)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin:14px 2px 0}
.linklike{background:none;border:none;color:var(--primary);font-size:13px;font-weight:600;cursor:pointer;padding:4px 0;font-family:var(--font)}
.linklike:hover{text-decoration:underline}
.iconbtn{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 10px;font-size:13px;color:var(--mut);cursor:pointer;font-family:var(--font)}
.iconbtn:hover{color:var(--primary);border-color:var(--primary)}
.iconbtn.active{background:var(--primary);color:#fff;border-color:var(--primary)}
.iconbtn:disabled{opacity:.45;cursor:default}
.btnrow{display:flex;gap:8px;flex-wrap:wrap}
.count{font-size:14px;color:var(--mut);margin:16px 2px}
.uni{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:16px;overflow:hidden}
.uni h2{font-size:16px;font-weight:600;margin:0;padding:13px 16px;background:var(--chip);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
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
@media (prefers-color-scheme: dark){mark{background:#5b4a1e;color:#fff}}
.foot{color:var(--mut);font-size:12px;margin-top:30px;text-align:center}
.star{cursor:pointer;color:var(--mut);user-select:none;font-size:15px}
.star.on{color:#e0a92b}
.statspanel{display:none;margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.statspanel.open{display:block}
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px}
.stath{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:var(--mut);margin:0 0 10px}
.barrow{margin-bottom:9px}
.barrow .top2{display:flex;justify-content:space-between;gap:8px;font-size:13px;margin-bottom:3px}
.barrow .top2 .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.barrow .top2 b{font-weight:700}
.barrow .bar{height:7px;border-radius:4px;background:var(--chip);overflow:hidden}
.barrow .bar>i{display:block;height:100%;background:var(--primary);border-radius:4px}
.multipanel{display:none;margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.multipanel.open{display:block}
.multipanel label{display:block;font-size:12px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.03em;margin-bottom:6px}
textarea{width:100%;min-height:84px;padding:10px 12px;font-family:var(--font);font-size:14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);resize:vertical}
textarea:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-tint)}
.matchbtn{margin-top:10px;padding:10px 18px;font-size:13px;font-weight:600;border-radius:8px;border:none;background:var(--primary);color:#fff;cursor:pointer;font-family:var(--font)}
.matchbtn:hover{background:var(--primary-dark)}
.rankuni{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:12px;padding:14px 16px}
.rankuni h3{margin:0;font-size:15px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center}
.rankuni .score{font-size:12px;font-weight:700;color:var(--primary);background:var(--primary-tint);padding:3px 10px;border-radius:20px;white-space:nowrap}
.rankuni .c{color:var(--mut);font-size:13px;margin-top:2px}
.codehit{margin-top:8px;font-size:13.5px}
.codehit b{color:var(--primary)}
.missing{margin-top:14px;font-size:13px;color:var(--mut)}
@media print{
  .searchbar,.toolbar,.statspanel,.multipanel,.foot,.filterbtn{display:none!important}
  body{background:#fff;color:#000}
  .uni{break-inside:avoid;border-color:#ccc}
  a{color:#000}
}
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
        <div class="field"><label>Sortering</label><select id="fSort">
          <option value="name">Universitet (A–Å)</option>
          <option value="hits">Flest treff</option>
          <option value="date">Nyeste behandling</option>
        </select></div>
      </div>
      <div class="checks">
        <label class="checkfield"><input type="checkbox" id="fShowRej"> Vis også strøkne/avslåtte emner</label>
        <label class="checkfield"><input type="checkbox" id="fFavOnly"> ★ Vis kun favoritter</label>
      </div>
      <div class="foot"><button class="reset" id="reset">Nullstill filtre</button></div>
    </div>
    <div class="toolbar">
      <button class="linklike" id="multiToggle" type="button">Søk med flere emnekoder samtidig →</button>
      <div class="btnrow">
        <button class="iconbtn" id="statsBtn" type="button">📊 Statistikk</button>
        <button class="iconbtn" id="copyLinkBtn" type="button">🔗 Kopier lenke</button>
        <button class="iconbtn" id="exportBtn" type="button">⬇ CSV</button>
        <button class="iconbtn" id="printBtn" type="button">🖨 Skriv ut / PDF</button>
      </div>
    </div>
    <div class="statspanel" id="statsPanel"></div>
    <div class="multipanel" id="multiPanel">
      <label for="multiInput">Lim inn NTNU-emnekoder (én per linje, eller kommaseparert)</label>
      <textarea id="multiInput" placeholder="TIØ4162&#10;TIØ4136&#10;TIØ4146…"></textarea>
      <button class="matchbtn" id="matchBtn" type="button">Finn universiteter</button>
    </div>
  </div>
  <div id="singleWrap">
    <div class="count" id="count"></div>
    <div id="results"></div>
  </div>
  <div id="multiWrap" style="display:none">
    <div class="count" id="multiCount"></div>
    <div id="multiResults"></div>
  </div>
  <div class="foot">Kilde: NTNU Wikihotell – rom «Utland» · __C__ · __U__ universiteter · __N__ registrerte vurderinger · bygget __DATE__.<br>
  Uoffisiell oversikt. Sjekk alltid mot den offisielle wiki-siden før du søker forhåndsgodkjenning.</div>
</div>
<script>
const DATA = __PAYLOAD__;
const q=document.getElementById('q'), results=document.getElementById('results'), count=document.getElementById('count');
const fCont=document.getElementById('fCont'), fCountry=document.getElementById('fCountry'), fUni=document.getElementById('fUni'), fSort=document.getElementById('fSort');
const fShowRej=document.getElementById('fShowRej'), fFavOnly=document.getElementById('fFavOnly');
const panel=document.getElementById('panel'), filterbtn=document.getElementById('filterbtn');
const singleWrap=document.getElementById('singleWrap'), multiWrap=document.getElementById('multiWrap');
const multiToggle=document.getElementById('multiToggle'), multiPanel=document.getElementById('multiPanel');
const multiInput=document.getElementById('multiInput'), matchBtn=document.getElementById('matchBtn');
const multiResults=document.getElementById('multiResults'), multiCount=document.getElementById('multiCount');
const statsBtn=document.getElementById('statsBtn'), statsPanel=document.getElementById('statsPanel');
const copyLinkBtn=document.getElementById('copyLinkBtn'), exportBtn=document.getElementById('exportBtn'), printBtn=document.getElementById('printBtn');

let multiMode=false;
let lastHits=[];

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
function esc(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function hl(s,term){s=esc(s);if(!term)return s;try{return s.replace(new RegExp('('+term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>');}catch(e){return s;}}
function gclass(g){const t=(g||'').toLowerCase();if(t.startsWith('ja')||t==='ok'||t.startsWith('godkj'))return 'g-ok';if(t.startsWith('nei')||t.startsWith('no'))return 'g-no';return '';}
function parseYear(s){const m=(s||'').match(/(19|20)\d{2}/);return m?parseInt(m[0],10):-1;}

// ---- favorites (localStorage) ----
const FAV_KEY='indok_favorites';
let favorites=new Set(JSON.parse(localStorage.getItem(FAV_KEY)||'[]'));
function isFav(u){return favorites.has(u);}
function toggleFav(u){
  if(favorites.has(u)) favorites.delete(u); else favorites.add(u);
  localStorage.setItem(FAV_KEY, JSON.stringify([...favorites]));
}
function starHtml(u){return `<span class="star ${isFav(u)?'on':''}" data-uni="${esc(u)}" title="Favoritt">${isFav(u)?'★':'☆'}</span>`;}
document.addEventListener('click', e=>{
  const star=e.target.closest('.star');
  if(!star) return;
  toggleFav(star.dataset.uni);
  refresh();
});

// ---- CSV export & print ----
function toCsv(rows){
  const headers=['Universitet','Land','Verdensdel','Vertsemnekode','Vertsemnenavn','Studiepoeng','NTNU-emnekode','NTNU-emnenavn','Emnetype','Godkjent','Behandlingsdato','Behandlet av','Kommentar','Strøket','URL'];
  const cell=v=>{v=(v==null?'':String(v));if(/[",;\n]/.test(v)) return '"'+v.replace(/"/g,'""')+'"'; return v;};
  const lines=[headers.join(';')];
  rows.forEach(r=>{
    lines.push([r.university,r.country,r.continent,r.fagkode_host,r.fagnavn_host,r.studiepoeng,r.ntnu_emnekode,r.ntnu_emnenavn,r.emnetype,r.godkjent,r.behandlingsdato,r.behandlet_av,r.kommentar,r.rejected?'Ja':'',r.url].map(cell).join(';'));
  });
  return lines.join('\r\n');
}
exportBtn.onclick=()=>{
  if(!lastHits.length) return;
  const csv='\uFEFF'+toCsv(lastHits);
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='utvekslingsemner.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
};
printBtn.onclick=()=>window.print();
copyLinkBtn.onclick=()=>{
  const done=()=>{const old=copyLinkBtn.textContent;copyLinkBtn.textContent='Lenke kopiert ✓';setTimeout(()=>copyLinkBtn.textContent=old,1500);};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(location.href).then(done).catch(()=>{});}
};

// ---- stats panel ----
function computeStats(){
  const uniCounts={}, countryCounts={};
  DATA.forEach(r=>{
    if(r.rejected) return;
    uniCounts[r.university]=(uniCounts[r.university]||0)+1;
    countryCounts[r.country]=(countryCounts[r.country]||0)+1;
  });
  const top=obj=>Object.entries(obj).sort((a,b)=>b[1]-a[1]).slice(0,10);
  return {unis:top(uniCounts), countries:top(countryCounts)};
}
function barList(pairs){
  const max=pairs.length?pairs[0][1]:1;
  return pairs.map(([name,n])=>`
    <div class="barrow">
      <div class="top2"><span class="lbl">${esc(name)}</span><b>${n}</b></div>
      <div class="bar"><i style="width:${Math.max(4,Math.round(n/max*100))}%"></i></div>
    </div>`).join('');
}
function renderStats(){
  const s=computeStats();
  statsPanel.innerHTML=`<div class="statgrid">
    <div><p class="stath">Flest godkjente emner — universitet</p>${barList(s.unis)}</div>
    <div><p class="stath">Flest godkjente emner — land</p>${barList(s.countries)}</div>
  </div>`;
}
statsBtn.onclick=()=>{
  const open=statsPanel.classList.toggle('open');
  statsBtn.classList.toggle('active', open);
  if(open && !statsPanel.dataset.rendered){renderStats(); statsPanel.dataset.rendered='1';}
};

// ---- URL sync ----
function syncUrl(){
  const p=new URLSearchParams();
  if(q.value.trim()) p.set('q', q.value.trim());
  if(fCont.value) p.set('cont', fCont.value);
  if(fCountry.value) p.set('country', fCountry.value);
  if(fUni.value) p.set('uni', fUni.value);
  if(fSort.value && fSort.value!=='name') p.set('sort', fSort.value);
  if(fShowRej.checked) p.set('rej','1');
  if(fFavOnly.checked) p.set('fav','1');
  const qs=p.toString();
  history.replaceState(null,'',location.pathname+(qs?('?'+qs):''));
}

function updateBtn(){
  const active=fCont.value||fCountry.value||fUni.value||fFavOnly.checked||fShowRej.checked||(fSort.value&&fSort.value!=='name');
  filterbtn.classList.toggle('active', !!active || panel.classList.contains('open'));
}

// ---- single search ----
function run(){
  const raw=q.value.trim();
  const term=raw.toUpperCase().replace(/\s+/g,'');
  const cont=fCont.value, country=fCountry.value, uni=fUni.value;
  const hideRej=!fShowRej.checked, onlyFav=fFavOnly.checked;
  let base=DATA.filter(r=>(!cont||r.continent===cont)&&(!country||r.country===country)&&(!uni||r.university===uni)&&(!hideRej||!r.rejected)&&(!onlyFav||isFav(r.university)));
  let hits=base;
  if(raw){
    hits=base.filter(r=>
      r.ntnu_codes.some(c=>c.includes(term)) ||
      r.ntnu_emnekode.toUpperCase().replace(/\s+/g,'').includes(term)
    );
  }
  syncUrl();
  updateBtn();
  const filtering = raw||cont||country||uni||onlyFav;
  if(!filtering){results.innerHTML='<div class="empty">Søk på en NTNU-emnekode, eller bruk filteret (&#7140;) for å bla etter land og universitet.</div>';count.textContent='';lastHits=[];exportBtn.disabled=true;return;}
  const groups={};
  hits.forEach(r=>{(groups[r.university]=groups[r.university]||{country:r.country,continent:r.continent,url:r.url,rows:[]}).rows.push(r);});
  let unis=Object.keys(groups);
  const sort=fSort.value;
  if(sort==='hits') unis.sort((a,b)=> groups[b].rows.length-groups[a].rows.length || a.localeCompare(b,'no'));
  else if(sort==='date') unis.sort((a,b)=> Math.max(...groups[b].rows.map(r=>parseYear(r.behandlingsdato))) - Math.max(...groups[a].rows.map(r=>parseYear(r.behandlingsdato))) || a.localeCompare(b,'no'));
  else unis.sort((a,b)=>a.localeCompare(b,'no'));
  count.textContent = hits.length ? `${hits.length} treff hos ${unis.length} universitet${unis.length>1?'er':''}` : '';
  lastHits=hits; exportBtn.disabled=!hits.length;
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
    return `<div class="uni"><h2><span>${starHtml(u)} ${esc(u)}</span><span class="c">${esc(g.country)} · ${esc(g.continent)} · <a href="${g.url}" target="_blank" rel="noopener">åpne wiki-side ↗</a></span></h2>${rows}</div>`;
  }).join('');
}

// ---- multi-code search ----
function parseCodes(text){
  return [...new Set(text.toUpperCase().split(/[^A-ZÆØÅ0-9]+/).map(s=>s.trim()).filter(s=>s.length>=4))];
}
function codeMatches(r, code){
  return r.ntnu_codes.some(c=>c.includes(code)) || (r.ntnu_emnekode||'').toUpperCase().replace(/\s+/g,'').includes(code);
}
function runMulti(){
  const codes=parseCodes(multiInput.value);
  const cont=fCont.value, country=fCountry.value, uni=fUni.value;
  const hideRej=!fShowRej.checked, onlyFav=fFavOnly.checked;
  let base=DATA.filter(r=>(!cont||r.continent===cont)&&(!country||r.country===country)&&(!uni||r.university===uni)&&(!hideRej||!r.rejected)&&(!onlyFav||isFav(r.university)));
  syncUrl();
  updateBtn();
  if(!codes.length){
    multiResults.innerHTML='<div class="empty">Lim inn NTNU-emnekoder (én per linje, eller kommaseparert), f.eks. TIØ4162, TIØ4136…</div>';
    multiCount.textContent=''; lastHits=[]; exportBtn.disabled=true; return;
  }
  const byUni={}; const flat=new Set();
  base.forEach(r=>{
    codes.forEach(code=>{
      if(codeMatches(r,code)){
        const e=byUni[r.university]=byUni[r.university]||{country:r.country,continent:r.continent,url:r.url,matches:{}};
        (e.matches[code]=e.matches[code]||[]).push(r);
        flat.add(r);
      }
    });
  });
  lastHits=[...flat]; exportBtn.disabled=!lastHits.length;
  const ranked=Object.entries(byUni).map(([uni,d])=>({uni,...d,hitCodes:Object.keys(d.matches)}))
    .sort((a,b)=>b.hitCodes.length-a.hitCodes.length || a.uni.localeCompare(b.uni,'no'));
  if(!ranked.length){
    multiResults.innerHTML='<div class="empty">Ingen universiteter (i gjeldende filter) har godkjente emner for noen av kodene du limte inn.</div>';
    multiCount.textContent=''; return;
  }
  multiCount.textContent=`${ranked.length} universitet${ranked.length>1?'er':''} dekker minst én av ${codes.length} emnekode${codes.length>1?'r':''}`;
  const coveredAnywhere=new Set();
  ranked.forEach(u=>u.hitCodes.forEach(c=>coveredAnywhere.add(c)));
  const missing=codes.filter(c=>!coveredAnywhere.has(c));
  multiResults.innerHTML=ranked.map(u=>{
    const rows=u.hitCodes.map(code=>u.matches[code].map(r=>
      `<div class="codehit"><b>${esc(code)}</b> — ${esc(r.fagnavn_host||r.fagkode_host||'(uten navn)')}${r.fagkode_host?` <span class="c">(${esc(r.fagkode_host)})</span>`:''}${r.rejected?' <span class="g-no">(strøket)</span>':''}</div>`
    ).join('')).join('');
    return `<div class="rankuni">
      <h3><span>${starHtml(u.uni)} ${esc(u.uni)}</span><span class="score">${u.hitCodes.length} av ${codes.length} koder</span></h3>
      <div class="c">${esc(u.country)} · ${esc(u.continent)} · <a href="${u.url}" target="_blank" rel="noopener">åpne wiki-side ↗</a></div>
      ${rows}
    </div>`;
  }).join('') + (missing.length ? `<div class="missing">Ingen treff (i gjeldende filter) for: ${missing.map(esc).join(', ')}</div>` : '');
}

function refresh(){ multiMode ? runMulti() : run(); }

multiToggle.onclick=()=>{
  multiMode=!multiMode;
  multiPanel.classList.toggle('open', multiMode);
  multiToggle.textContent = multiMode ? '← Tilbake til enkeltsøk' : 'Søk med flere emnekoder samtidig →';
  singleWrap.style.display = multiMode ? 'none' : '';
  multiWrap.style.display = multiMode ? '' : 'none';
  refresh();
};
matchBtn.onclick=runMulti;
multiInput.addEventListener('input', ()=>{ if(multiMode) runMulti(); });

fCont.addEventListener('change',()=>{refreshDropdowns();refresh();});
fCountry.addEventListener('change',()=>{refreshDropdowns();refresh();});
fUni.addEventListener('change',refresh);
fSort.addEventListener('change',refresh);
fShowRej.addEventListener('change',refresh);
fFavOnly.addEventListener('change',refresh);
q.addEventListener('input',()=>{ if(!multiMode) run(); });
document.getElementById('reset').onclick=()=>{
  q.value=''; fCont.value=''; fCountry.value=''; fUni.value='';
  fSort.value='name'; fShowRej.checked=false; fFavOnly.checked=false;
  refreshDropdowns(); refresh();
};

// ---- init from URL ----
(function init(){
  const p=new URLSearchParams(location.search);
  if(p.get('cont')) fCont.value=p.get('cont');
  refreshDropdowns();
  if(p.get('country')) fCountry.value=p.get('country');
  refreshDropdowns();
  if(p.get('uni')) fUni.value=p.get('uni');
  if(p.get('q')) q.value=p.get('q');
  if(p.get('sort')) fSort.value=p.get('sort');
  if(p.get('rej')==='1') fShowRej.checked=true;
  if(p.get('fav')==='1') fFavOnly.checked=true;
  run();
})();
</script>
</body>
</html>'''

html=html.replace('__PAYLOAD__',payload).replace('__DATE__',built).replace('__N__',str(len(data))).replace('__U__',str(nuni)).replace('__C__', ' · '.join(dict.fromkeys(r['continent'] for r in data)))
out=os.path.join(ROOT,'index.html')
open(out,'w',encoding='utf-8').write(html)
print('wrote',out,round(os.path.getsize(out)/1024),'KB')

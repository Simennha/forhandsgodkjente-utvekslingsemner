#!/usr/bin/env python3
"""
ntnu_indok.py — henter og strukturerer forhåndsgodkjente utvekslingsemner for
Indøk fra NTNU Wiki (rom «Utland»).

Struktur på wikien:
  Indøk (root) -> Kontinent (Europa/Asia/...) -> Land -> Universitet (tabell)

To trinn:
  1) DISCOVER  – gå gjennom sidetreet og finn universitetssidene (med land).
  2) EXTRACT   – hent hver universitetsside via Confluence «viewsource» og
                 parse tabellen til strukturerte rader.

Bruk:
  python ntnu_indok.py discover --root 101092381 --out universities_europe.json
  python ntnu_indok.py extract  --catalog universities_europe.json --out data_europe.json
  python ntnu_indok.py build    --data data_europe.json --html indok-utveksling.html

Kontinent-rot-IDer (barn av Indøk-siden 97257718):
  Europa 101092381 · Asia 107250332 · Amerika 107250743 · Afrika 101093756
  Australia/NZ 107250722 · Canada 146607325
"""
import json, re, os, sys, time, argparse, urllib.request, urllib.parse
from bs4 import BeautifulSoup

WIKI = "https://www.ntnu.no/wiki"
CHILDREN = (WIKI + "/plugins/pagetree/naturalchildren.action"
            "?decorator=none&excerpt=false&sort=position&reverse=false"
            "&disableLinks=false&expandCurrent=true&hasRoot=true"
            "&pageId={}&treeId=0&startDepth=0")
SRC  = WIKI + "/plugins/viewsource/viewpagesrc.action?pageId={}"
VIEW = WIKI + "/pages/viewpage.action?pageId={}"
UA = {'User-Agent': 'Mozilla/5.0 (NTNU-Indok-extractor)'}
CODE_RE = re.compile(r'[A-ZÆØÅ]{2,4}\d{4}')

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode('utf-8','replace')

# ---------- DISCOVER ----------
PAGE_HREF = re.compile(r'/pages/(\d+)/')
def children(pid):
    """Return list of (title, page_id) direct children (parses the HTML fragment)."""
    html = get(CHILDREN.format(pid)); time.sleep(0.3)
    soup = BeautifulSoup(html, 'html.parser')
    out=[]; seen=set()
    for a in soup.find_all('a', href=True):
        m = PAGE_HREF.search(a['href'])
        if not m: continue
        cid=m.group(1); title=re.sub(r'\s+',' ',a.get_text(' ',strip=True)).strip()
        if cid in seen or not title or title=='#': continue
        seen.add(cid); out.append((title, cid))
    return out

def _clean(t):
    return re.sub(r'^Indøk\s*-?\s*','',t).strip(' -')

def discover(root, name=None):
    """Auto-detects tree depth. Some continents are Continent->Land->Universitet
    (Europa/Asia/Amerika); others link universities directly under the continent
    (Canada/Afrika/Australia). Direct leaves are bucketed under `name`."""
    countries=[]; direct=[]
    for ctitle, cid in children(root):
        grand=children(cid)
        if grand:
            countries.append({'country':_clean(ctitle),'page_id':cid,
                              'universities':[{'name':_clean(t),'page_id':u} for t,u in grand]})
        else:
            direct.append({'name':_clean(ctitle),'page_id':cid})
    if direct:
        countries.append({'country':(name or 'Direkte'),'page_id':root,'universities':direct})
    return {'root_page_id':root,'countries':countries}

# ---------- EXTRACT ----------
def norm_codes(text):
    up=text.upper().replace(' ','')
    codes=list(dict.fromkeys(CODE_RE.findall(up)))
    for m in re.finditer(r'([A-ZÆØÅ]{2,4})(\d{4})/(\d{2,4})', up):
        pre,first,suf=m.groups()
        c=pre+first[:2]+suf if len(suf)==2 else pre+suf.zfill(4)
        if c not in codes: codes.append(c)
    return codes

def map_headers(cells):
    idx={}; low=[c.lower().strip() for c in cells]
    for i,c in enumerate(low):
        if 'ntnu' in c:
            if 'navn' in c and 'ntnu_emnenavn' not in idx: idx['ntnu_emnenavn']=i
            elif 'ntnu_emnekode' not in idx: idx['ntnu_emnekode']=i
    def has(c,*k): return any(x in c for x in k)
    for i,c in enumerate(low):
        if 'ntnu' in c: continue
        if 'fagkode_host' not in idx and (has(c,'fagkode','emnekode','emenkode','emnkode','course code') or (c.startswith('emne') and 'navn' not in c and 'type' not in c)): idx['fagkode_host']=i; continue
        if 'fagnavn_host' not in idx and has(c,'fagnavn','emnenavn','course','title'): idx['fagnavn_host']=i; continue
        if 'studiepoeng' not in idx and has(c,'stp','ects','hp','credit','studiepoeng'): idx['studiepoeng']=i; continue
        if 'emnetype' not in idx and has(c,'emnetype'): idx['emnetype']=i; continue
        if 'godkjent' not in idx and has(c,'godkj','gokj','godj'): idx['godkjent']=i; continue
        if 'behandlingsdato' not in idx and has(c,'behandlingsdato','dato'): idx['behandlingsdato']=i; continue
        if 'behandlet_av' not in idx and has(c,'behandlet','fagansvarlig','faglærer','fagl','ansvarlig'): idx['behandlet_av']=i; continue
        if 'kommentar' not in idx and has(c,'kommentar','merknad','comment'): idx['kommentar']=i; continue
    return idx

def ctext(td): return re.sub(r'\s+',' ',td.get_text(' ',strip=True)).strip()
def struck(td): return bool(td.find(['s','del','strike'])) or 'line-through' in (td.get('style','') or '')

def parse_page(html, info, pid):
    soup=BeautifulSoup(html,'html.parser'); recs=[]
    for table in soup.find_all('table'):
        hidx=None
        for tr in table.find_all('tr'):
            cells=tr.find_all(['th','td']); texts=[ctext(c) for c in cells]
            low=' '.join(texts).lower()
            if hidx is None:
                if (any(k in low for k in ('godkj','gokj','godj')) or 'ntnu' in low) and any(k in low for k in ('fagkode','emnekode','emenkode','emnkode','emnenavn','emne','navn')):
                    hidx=map_headers(texts)
                continue
            if not any(texts): continue
            def g(f):
                i=hidx.get(f); return texts[i] if (i is not None and i<len(texts)) else ''
            def sk(f):
                i=hidx.get(f); return struck(cells[i]) if (i is not None and i<len(cells)) else False
            code_cell=g('ntnu_emnekode')
            rec={'ntnu_codes':norm_codes(code_cell),'fagkode_host':g('fagkode_host'),
                 'fagnavn_host':g('fagnavn_host'),'studiepoeng':g('studiepoeng'),
                 'ntnu_emnekode':code_cell,'ntnu_emnenavn':g('ntnu_emnenavn'),
                 'emnetype':g('emnetype'),'godkjent':g('godkjent'),
                 'behandlingsdato':g('behandlingsdato'),'behandlet_av':g('behandlet_av'),
                 'kommentar':g('kommentar'),
                 'rejected':sk('ntnu_emnekode') or sk('godkjent') or sk('fagkode_host'),
                 'university':info['university'],'country':info['country'],
                 'page_id':pid,'url':VIEW.format(pid)}
            if rec['ntnu_codes'] or rec['fagkode_host'] or rec['ntnu_emnekode']:
                recs.append(rec)
    return recs

def extract(catalog, cache_dir='raw'):
    os.makedirs(cache_dir, exist_ok=True)
    meta=json.load(open(catalog,encoding='utf-8')); rows=[]; stats=[]
    for co in meta['countries']:
        for u in co['universities']:
            info={'university':u['name'],'country':co['country']}
            fp=os.path.join(cache_dir,u['page_id']+'.html')
            try:
                if os.path.exists(fp) and os.path.getsize(fp)>200:
                    html=open(fp,encoding='utf-8').read()
                else:
                    html=get(SRC.format(u['page_id'])); open(fp,'w',encoding='utf-8').write(html); time.sleep(0.4)
                r=parse_page(html,info,u['page_id']); rows.extend(r); stats.append((co['country'],u['name'],len(r)))
            except Exception as e:
                stats.append((co['country'],u['name'],'ERR '+str(e)))
    return rows, stats

# ---------- BUILD (see build_app.py for the full template) ----------
def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='cmd')
    d=sub.add_parser('discover'); d.add_argument('--root',required=True); d.add_argument('--out',required=True); d.add_argument('--name',default=None)
    e=sub.add_parser('extract'); e.add_argument('--catalog',required=True); e.add_argument('--out',required=True)
    a=ap.parse_args()
    if a.cmd=='discover':
        cat=discover(a.root, a.name); json.dump(cat,open(a.out,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
        n=sum(len(c['universities']) for c in cat['countries'])
        print(f'Discovered {len(cat["countries"])} land / {n} universiteter -> {a.out}')
    elif a.cmd=='extract':
        rows,stats=extract(a.catalog)
        json.dump(rows,open(a.out,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
        print(f'TOTAL {len(rows)} rader -> {a.out}')
        for s in stats: print('  ',s[2],'\t',s[0],s[1])
    else:
        ap.print_help()

if __name__=='__main__': main()

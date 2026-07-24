---
name: ntnu-indok-emner
description: >
  Henter og strukturerer forhåndsgodkjente utvekslingsemner for Industriell
  økonomi og teknologiledelse (Indøk) fra NTNU Wiki (rom «Utland»). Bruk når
  noen vil slå opp hvilke universiteter som har fått et emne godkjent som et
  gitt NTNU-emne, eller vil oppdatere/utvide datagrunnlaget (f.eks. nye
  kontinenter). Trigger på: «Indøk utveksling», «godkjente emner», «fagkode
  utland», «NTNU emnekode utveksling».
---

# NTNU Indøk – utvekslingsemner

## Hva dette er
NTNU-wikien lister, per universitet, hvilke emner i utlandet som tidligere er
godkjent som erstatning for et NTNU-emne. Denne skillen går gjennom sidetreet,
henter tabellene og gjør dem søkbare på NTNU-fagkode.

## Sidestruktur på wikien
```
Indøk (97257718)
 └─ Kontinent      Europa 101092381 · Asia 107250332 · Amerika 107250743
    │              Afrika 101093756 · Australia/NZ 107250722 · Canada 146607325
    └─ Land (f.eks. «Indøk - Danmark»)
       └─ Universitet  ← tabellen med emner ligger her
```

## Nyttige endepunkter (offentlige, ingen innlogging)
- Barn av en side (HTML-fragment):
  `/wiki/plugins/pagetree/naturalchildren.action?decorator=none&excerpt=false&sort=position&reverse=false&disableLinks=false&expandCurrent=true&hasRoot=true&pageId=<ID>&treeId=0&startDepth=0`
  (Alle parametrene må være med, ellers svarer den tomt.)
- Ren tabellkilde for en side (lite støy, best for parsing):
  `/wiki/plugins/viewsource/viewpagesrc.action?pageId=<ID>`
- Lesbar side: `/wiki/pages/viewpage.action?pageId=<ID>`

## Tabellformat (varierer mellom sider!)
Kolonneoverskriftene er ikke standardiserte. Parseren mapper på nøkkelord:
- Vertsemnekode: «Fagkode» / «Emnekode» / «Emne» / «Emenkode»
- Vertsemnenavn: «Fagnavn» / «Emnenavn»
- Studiepoeng: «Stp» / «ECTS» / «HP»
- NTNU-kode: enhver kolonne som inneholder «NTNU» (uten «navn»)
- NTNU-navn: «NTNU … navn»
- Godkjent: «Godkjent» / «Godkj»
- Dato: «Behandlingsdato» / «Dato»
- Saksbehandler: «Behandlet av» / «Fagansvarlig» / «Faglærer»
- Kommentar: «Kommentar» / «Merknad»

Spesialtilfeller som håndteres: koder som `TIØ4161/62` utvides til
`TIØ4161` + `TIØ4162`; `TIØ4161*` normaliseres; strøkne rader (`<s>`,
gjennomstreking) markeres som `rejected:true`.

## Slik kjører du
Krever Python 3 + `beautifulsoup4` (`pip install beautifulsoup4`).

```bash
# 1) Finn universitetssidene for et kontinent (bygger katalogen)
python ntnu_indok.py discover --root 101092381 --out universities_europe.json

# 2) Hent og parse alle tabellene -> strukturert JSON
python ntnu_indok.py extract --catalog universities_europe.json --out data_europe.json

# 3) Bygg den søkbare HTML-appen (data bakes inn i én fil)
python build_app.py           # leser data_europe.json i samme mappe
```

Rå HTML caches i `raw/<pageId>.html` slik at gjenkjøring går raskt og skånsomt
mot wikien. Slett `raw/` for å tvinge ny henting.

## Utvide til andre kontinenter
Kjør `discover` med en annen `--root` (se ID-ene over), slå sammen katalogene,
og kjør `extract` på nytt. Prioritert rekkefølge så langt: Europa (ferdig).

## Datamodell (hver rad i data_*.json)
`ntnu_codes[]` (normaliserte koder for oppslag), `fagkode_host`, `fagnavn_host`,
`studiepoeng`, `ntnu_emnekode`, `ntnu_emnenavn`, `emnetype`, `godkjent`,
`behandlingsdato`, `behandlet_av`, `kommentar`, `rejected`, `university`,
`country`, `page_id`, `url`.

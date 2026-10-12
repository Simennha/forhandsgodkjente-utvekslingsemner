# Beslutninger (DECISIONS)

Alle valg jeg tok uten å spørre. Format: valg, begrunnelse, alternativer. Nummereres fortløpende
og oppdateres per fase.

## D-001 Repo-plassering
- **Valg:** Prosjektet ligger i mappen `charlie/` i det eksisterende repoet, på grenen
  `claude/charlie-lola-build-kalspv`. Mappen er selvstendig (egen Makefile, pyproject, CI).
- **Begrunnelse:** Arbeidsmiljøet mitt har bare skrivetilgang til dette repoet og denne grenen.
- **Alternativer:** Eget repo. Flytt med `git subtree split --prefix charlie -b charlie-only`
  og push til et nytt repo; da blir `.github/workflows/ci.yml` aktiv automatisk.

## D-002 Én pakke, ti tjenester
- **Valg:** Én Python-pakke `assistants` og ett Docker-image. Hver container starter én tjeneste
  (`python -m assistants.services.run <navn>`). Isolasjon kommer fra nett, hemmeligheter og
  database-roller per container, ikke fra separate kodebaser.
- **Begrunnelse:** Mindre duplisering, delte typer og én testsuite. Sikkerheten avhenger ikke av
  hvilken kode som finnes i imaget, men av hva containeren kan nå og hvilke nøkler den får.
- **Alternativer:** Ett image per tjeneste (mindre angrepsflate per image, mer vedlikehold).

## D-003 Python 3.12 lokalt via uv
- **Valg:** `.venv` med Python 3.12 (uv). Docker-image `python:3.12-slim`.
- **Alternativer:** Systemets Python 3.13 (avvist: kravet sier 3.12).

## D-004 Testdatabase
- **Valg:** `make test` starter en ren Postgres 16 i Docker (`charlie-testdb`, port 54329). Hver
  testsesjon lager en ny database, kjører alle migrasjoner og seed. `CHARLIE_REQUIRE_DB=1`
  gjør at database-tester **feiler** (ikke hoppes over) hvis databasen mangler.
- **Begrunnelse:** Ærlig grønt: ingen stille hopp over RLS- og loggtester.
- **Alternativer:** Supabase CLI (`supabase start`) – migrasjonene ligger i `supabase/migrations`
  og fungerer der også, men CLI-en krever flere containere og er tregere i CI.

## D-005 Roller i stedet for Supabase-standardrollene
- **Valg:** Egne roller `charlie_agent`, `lola_agent`, `executor_svc`, `gateway_svc`, `worker_svc`,
  `extractor_svc` (uten noen tilganger). RLS slås på og tvinges (`force row level security`).
- **Begrunnelse:** `anon`/`authenticated`/`service_role` er laget for nettleserklienter. Her
  kobler bare tjenestene våre seg til, og hver skal ha minst mulig.

## D-006 Uforanderlig logg: hash beregnes i databasen
- **Valg:** En `BEFORE INSERT`-trigger tar en advisory-lås, tildeler `seq` *etter* låsen, og
  beregner `hash = sha256(prev_hash|seq|ts|actor|kind|data)`. `UPDATE`, `DELETE` og `TRUNCATE`
  blokkeres av triggere (som også slår inn for eieren). `audit.verify_chain()` sjekker hele kjeden.
- **Begrunnelse:** Kalleren kan verken velge eller forfalske hash. Å tildele `seq` etter låsen
  gjør at rekkefølgen i kjeden alltid er lik `seq`-rekkefølgen, også ved samtidige skrivinger.
- **Restrisiko:** En database-superbruker kan slå av triggere. Derfor eksporteres loggen også til
  objektlagring med object lock (WORM), og eksporten signeres.

## D-007 Kill switch: lease-modell, feiler lukket
- **Valg:** Enheten signerer en kortlivet «lease» (Ed25519, 30 s) med tilstanden. Alle tjenester
  som kan handle krever gyldig ACTIVE-lease. Mangler eller er lease ugyldig/utløpt, behandles
  det som `PAUSED`. Gamle leaser kan ikke spilles av på nytt (stigende sekvensnummer).
- **Begrunnelse:** Kutter noen nettet eller strømmen til enheten, stopper Charlie av seg selv.
  Det er aldri motsatt: tilbakekomst av en ACTIVE-lease gjenoppretter ikke frosne forslag.
- **Alternativer:** Push-meldinger uten lease (avvist: feiler åpent hvis meldingen ikke kommer fram).

## D-008 Ring-regler tolket slik
- To ring innen 2 min fra **samme** ukjente nummer → `PAUSED`.
- Ett ring fra kjent nummer → `PAUSED`.
- Tre ring totalt (fra hvilke som helst numre) innen stoppvinduet (10 min) → `STOPPED`.
- SMS med riktig kode (fra hvilket som helst nummer) → `STOPPED`.
- Fysisk bryter → `STOPPED` (konfigurerbart). Alle tall i `config.yaml`.
- **Begrunnelse:** «Tredje ring» tolkes strengt som tredje ring uansett avsender, fordi en
  angriper ellers kunne unngå stopp ved å bytte nummer. At noen kan *stoppe* Charlie ved å ringe
  tre ganger er bevisst: feilen er da tilgjengelighet, ikke sikkerhet.

## D-009 Gjenoppstart krever alle fire betingelser, også fra PAUSED
- **Valg:** `ACTIVE` nås bare med riktig PIN **og** signert bekreftelse fra en annen enhet **og**
  attest om at loggen er gjennomgått (signaturen dekker hash-en til loggens siste rad) **og**
  at nøklene er byttet (enheten sammenligner aktive nøkkel-ID-er med de som var aktive da
  hendelsen startet). Gjelder både fra `PAUSED` og `STOPPED`.
- **Begrunnelse:** Kravet sier «alle tre betingelsene» (PIN, annen enhet, logg+nøkler). Å kreve
  nøkkelbytte også etter PAUSED er strengere enn nødvendig, men en pause skyldes ofte mistanke om
  kompromittering, og en enkel regel er lettere å stole på.
- **Alternativer:** Lettere gjenoppstart fra PAUSED (avvist).

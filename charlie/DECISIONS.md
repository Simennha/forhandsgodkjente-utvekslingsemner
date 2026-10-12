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

## D-010 Også eierens egen tale går gjennom uttrekkeren
- **Valg:** Transkripsjon av samtaler (også eierens) går først til uttrekkeren. Routeren (Haiku)
  og Sonnet ser bare det validerte uttrekket og strukturerte verktøyresultater.
- **Begrunnelse:** Kravet sier at uttrekkeren leser transkripsjon, og at modellen som handler
  aldri ser rå tekst utenfra. En forfalsket eller manipulert samtale blir dermed behandlet
  likt med e-post. Koster ett ekstra Haiku-kall per tur (svært billig).
- **Alternativer:** La routeren lese eierens tale direkte (avvist: større angrepsflate).

## D-011 Deterministisk mock-LLM med to personligheter
- **Valg:** `assistants/agents/mock_brains.py` har en «lydig» og en «kompromittert» variant.
  Den kompromitterte adlyder all injeksjon, foreslår nivå 4, lekker og lover. Alle tester og
  `make eval` kjøres med begge.
- **Begrunnelse:** Beviser at sikkerheten ikke avhenger av modellen. Ekte modell kun via `make eval-live`.

## D-012 Charlie har ingen nøkler – heller ikke LLM-nøkkelen
- **Valg:** agents-charlie og extractor kaller LLM via utførerens `/llm`-proxy, som holder
  Claude-nøkkelen og håndhever kostnadstaket. Lola har sin egen nøkkel og sitt eget tak
  (kravet sier «egne nøkler»), men ingen nettvei til noe av Charlies.

## D-013 Gateway-signert samtalekontekst
- **Valg:** Gatewayen signerer {tillit, modus, kanal, sesjon, utløp} med HMAC (nøkkel kun hos
  gateway og utfører). agents-charlie sender signaturen videre uendret; utføreren verifiserer.
- **Begrunnelse:** En kompromittert agent-container kan ikke gi seg selv PIN-nivå.

## D-014 Beløp og bekreftelsestekst lages av koden
- **Valg:** Bestillinger har ikke prisfelt; beløpet regnes ut fra lagrede priser i
  `lager.artikkel`. `email.send` inneholder hele meldingen (ikke en utkast-ID), slik at
  bekreftelsen viser nøyaktig det som sendes og er bundet til SHA-256 av parameterne.

## D-015 Tvangskode
- **Valg:** Tastes som PIN (DTMF). Samme svar («Takk, PIN godkjent.»), samme loggrad
  (`auth.pin_verified`), samme tidsbruk (begge hash-sammenligninger kjøres alltid). Bare
  `duress_safe`-verktøy virker (status, klokke, vær). Kill switch-enheten sender stille alarm
  (SMS + push) til det andre nummeret over sitt eget mobilnett. Hendelser lagres som `owner_pin`.
  Koden lagres bare som saltet og «pepret» scrypt-hash og forekommer aldri i logger, hendelser,
  modellkall eller svar (test: `tests/test_identity.py`).
- **Gjelder bare Charlie.** Lolas linje har ingen PIN eller tvangskode.

## D-016 PIN
- scrypt (n=2^14) av HMAC(pepper, kode), egen salt per kode. Tre feil gir 15 min sperre per
  nummer. PIN sendes aldri i chat/SMS; chat i appen bruker passkey-innlogging (= nummer + PIN).

## D-017 Ukjent nummer på Charlies linje
- **Valg:** Kort beskjed, ingen samtykkemelding, ingen transkripsjon, samtalen registreres ikke.
  Gjestemodus gjelder automatisk for ukjente enheter.

## D-018 Åpent verksted: ingen tale til tekst i det hele tatt (standard)
- **Valg:** `workshop.open_workshop.allow_phone_stt: false`. Også telefon og dørtelefon får
  bare en innspilt beskjed om å sende SMS. Kan slås på i konfig hvis du vil.
- **Begrunnelse:** Strengeste tolkning av «ingen lydbehandling i denne modusen».

## D-019 Godkjenningsenheter
- **Valg:** Simulert telefon-passkey og godkjenningsboks signerer med Ed25519. Ekte WebAuthn-
  verifisering (`WebAuthnVerifier`, py_webauthn) og en minimal PWA finnes, men er ikke testet
  mot en ekte telefon. Godkjenningsboksen er en egen enhet med skjerm, knapp og nøkkel i
  secure element; protokollen er den samme som simulatoren bruker.

## D-020 Loggskriving via `audit.append()`
- Skrivere har bare INSERT, ikke SELECT, på `audit.log`. `audit.append()` (security definer)
  setter inn én rad og returnerer bare den radens kjedefelt.

## D-021 Twilio-flyt
- Opptak (`<Record>`) lastes ned og transkriberes lokalt (faster-whisper). Svar spilles med
  lokal Piper via `<Play>`; uten Piper faller vi tilbake til Twilios `<Say>`. Ikke verifisert
  mot ekte Twilio.

## D-022 Samme klasser i prosess og i tjenester
- `assistants.sim.SimSystem` kobler alt i én prosess med simulatorer (tester, eval).
  Tjenestene i `assistants/services/` kobler de samme klassene med Postgres og HTTP.

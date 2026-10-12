# Driftshåndbok (runbook)

Gjelder mini-PC-en (Linux) med Docker Compose bak WireGuard, og kill switch-enheten.

## Daglig (2 min, gjerne automatisk varsel)

- [ ] Sjekk at du ikke har fått varsel om stille tjenester (heartbeat) eller anomalier.
- [ ] Kostnad i dag: `docker compose exec worker python -c "import httpx;print(httpx.get('http://localhost:8000/costs').json())"`.
      Charlie under 100 kr/dag, Lola under 20 kr/dag (hard grense; agenten pauser selv).
- [ ] Ventende godkjenninger i PWA-en: godkjenn eller la dem gå ut (5 min / 10 min for boksen).
- [ ] Kill switch-LED er av (ACTIVE).

## Ukentlig (15 min)

- [ ] Les ukerapporten (lages mandag 07:00 av worker og lagres som notat).
- [ ] Gå gjennom `action.denied`-rader i loggen siste uke:
      `select ts, data from audit.log where kind = 'action.denied' and ts > now() - interval '7 days';`
      Mange nektelser fra samme kanal kan bety et angrep eller et feilkonfigurert verktøy.
- [ ] `select * from audit.verify_chain();` skal gi `ok = t`.
- [ ] Sjekk at loggeksporten til objektlagring går (`GET /audit/verify` på worker viser `exported_to_seq`).
- [ ] Lager: se innkjøpsforslag per lokasjon (Charlie: «Hva bør vi bestille i Norge/Sveits?»).
- [ ] Oppdater containerne ved sikkerhetsfikser (`docker compose pull` for `postgres:16`, bygg på nytt).

## Månedlig (30–45 min) – nødstopp, varsler, backup og tvangskode

1. `make monthly-test` (simulator) – skal være grønn.
2. `python -m assistants.ops.monthly --compose` – gjenoppretter `pg_dump` i en kladdebase og
   verifiserer hash-kjeden. Skal være grønn.
3. Kill switch på den ekte enheten: følg testprosedyren i `docs/killswitch.md`, minst trinn
   2, 3, 8 og 9 (ring fra kjent nummer, gjenoppstart, nettbrudd, tvangskode).
4. Varsler: trekk nettverkskabelen til `agents-lola` (eller stopp containeren) i 3 minutter –
   du skal få varsel om stille tjeneste.
5. Tvangskode: ring Charlie, tast tvangskoden. Kontroller at svaret ser normalt ut, at bare
   harmløse svar gis, og at personen med det andre nummeret fikk stille alarm. Avtal på forhånd
   med vedkommende at dette er en test.
6. Oppdater valutakursen `llm.usd_to_nok` i `config.yaml` og sammenlign kostnadsvisningen med
   Anthropic-konsollen.
7. Skriv kort i driftsloggen: dato, hva som ble testet, resultat.

## Kvartalsvis (1–2 timer)

- [ ] Bytt tjenestetokener og interne nøkler: `make secrets` med `--force` i et vedlikeholdsvindu
      (ny `action.hmac_key`, `ctx.hmac_key`, tjenestetokener, rollepassord), deretter
      `docker compose up -d`. Leverandørnøkler byttes i konsollene.
- [ ] Gå gjennom `policy/levels.yaml` og `config.yaml` (tak, rate, numre). Endringer krever
      grønn `make test` før de tas i bruk.
- [ ] Kjør `make eval` (og eventuelt `make eval-live` med et lite budsjett) og les avvikene.
- [ ] Gjenopprett en full backup til en egen maskin og start systemet derfra (ikke bare loggen).
- [ ] Kontroller at passkey-registreringene bare gjelder enheter du fortsatt har.
- [ ] Bytt PIN og tvangskode (ny `make secrets` for identitet, eller rediger hashene).
- [ ] Bytt batteri på kill switch-enheten hvis kapasiteten er under 80 %.

## Hendelser

### Kill switch utløst
1. Ikke start på nytt før du vet hvorfor. Les loggen fra og med `PAUSED/STOPPED`-raden.
2. STOPPED: nøklene er inaktive og serveren er uten Internett. Bytt alle nøkler.
3. Følg gjenoppstartsprosedyren i `docs/killswitch.md`. Frosne forslag må foreslås på nytt.

### Stille alarm mottatt (tvangskode)
Personen med det andre nummeret ringer eieren og/eller politiet etter avtalt plan. Ikke ring
Charlie-nummeret for å «sjekke» – det kan avsløre at alarmen er gått.

### Tapt telefon
1. Ring det hemmelige kill switch-nummeret fra en annen telefon (ett ring fra kjent nummer
   virker ikke da – ring to ganger innen 2 minutter, eller send SMS-koden).
2. Slett passkey-registreringen for telefonen, bytt PIN.
3. Gjenoppstart etter prosedyren.

### Kostnadstak nådd
Agenten pauser seg selv til neste dag/måned. Sjekk `GET /costs` og loggen (`llm.call`) for
hvilken kanal som drev kostnaden. Øk taket i `config.yaml` bare hvis bruken var legitim.

### Mistanke om prompt injection
Finn uttrekket (`core.transcript_extracts` eller loggens `lola_queue.processed` med flagg).
Ingenting skal ha skjedd uten din godkjenning; sjekk `action.executed` i samme tidsrom.

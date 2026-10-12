# Red team-rapport

## Metode

1. **Uavhengig gjennomgang:** En egen underagent som ikke hadde sett koden, fikk bare
   oppdragets invarianter og repoet, med beskjed om å angripe påstanden «ingen modell kan
   overtales til mer tilgang». Den leste koden, kjørte testene og skrev egne angrepsskript mot
   simulatoren (`SimSystem`). Den endret ingen filer. Den fant 18 funn; hvert funn er
   kontrollert mot koden og de fleste med et kjørbart angrep.
2. **Egne funn under byggingen:** Live-prober i Docker Compose, skanneren for nivå 4 og
   integrasjonstestene avslørte feil som ble rettet underveis (S1–S10 under).
3. **Kontinuerlig:** `make eval` kjører 56 norske angrepssetninger mot hele pipelinen med både
   en lydig og en *fullt kompromittert* mock-modell. Resultat: 0 harde brudd.

Hvert rettet funn har en regresjonstest i `tests/test_redteam_regressions.py` (eller i testen
som er nevnt).

## Funn fra den uavhengige gjennomgangen

| # | Alvor | Funn | Status | Rettelse / test |
|---|---|---|---|---|
| 1 | **Høy** | Kill switch-enheten startet i ACTIVE etter omstart; nye leaser ble godtatt når telleren passerte den gamle. Charlie kom tilbake uten PIN, annen enhet eller nøkkelbytte | **Rettet** | Tilstand og lease-teller lagres atomisk (`FileDeviceState`) før ny lease sendes. Manglende tilstand uten provisjonering eller ulesbar fil → STOPPED. Teller er tidsbasert og bare økende. `tests/killswitch/test_device.py::test_restart_does_not_resume`, `test_corrupt_state_boots_stopped`, `test_missing_state_without_provisioning_boots_stopped` |
| 2 | Middels–høy | Åpent verksted gikk tapt ved omstart av gatewayen; mikrofonene kom tilbake | **Rettet** | Modus lagres; ukjent modus ved oppstart = mikrofoner kuttet til eieren setter modus. Utføreren starter i Åpent verksted og synkes hvert 20. sekund. `test_mode_survives_restart_and_unknown_means_mics_cut` |
| 3 | Middels | Tvangskode kunne oppdages: andre svar enn med PIN, og et merkbart synkront kall | **Rettet (delvis)** | Under tvang beskrives nektede handlinger slik en PIN-økt ville sett dem («Bekreft med passkey …», «Gjort …»), men ingenting utføres. Signalet til kill switch sendes asynkront; loggmeldingen er nøytral. Svarene er nå ord for ord like (`test_identity.py::test_duress_looks_normal…`). **Rest:** revisjonsloggen viser `action.denied` med årsak `not_available`. Loggen er ikke tilgjengelig for den som ringer; den er beholdt fordi etterpåkontroll er viktigere |
| 4 | Middels | Billig å tvinge PAUSED (falske PIN-forsøk under sperre, vanlige «krever PIN»-svar, kostnadsregel talte kall) | **Rettet** | Forsøk under sperre telles ikke (`auth.locked`). «Krever PIN / feil modus» teller ikke som angrep. `cost_spike` måles i kroner, ikke antall kall. Tre regresjonstester. **Rest:** gjenoppstart etter en pause krever fortsatt nøkkelbytte (bevisst, D-009) |
| 5 | Middels | Fritekst-parametre fra utenforstående (f.eks. e-postens «body») nådde Charlies modell | **Rettet** | Bare eierens egne ord kan bære parametre (`TRUSTED_SOURCES`). Resultater fra e-post, kø og samtaler reduseres til rensede oppsummeringer og flagg. `test_outsider_parameters_do_not_reach_router`. **Rest:** en oppsummering formidler fortsatt innholdet (det er hele poenget), så en angriper kan påvirke *ordlyden* i svaret til eieren, men ikke handlinger |
| 6 | Middels | PIN, tvangskode og testord kan knekkes offline hvis `gateway.json` lekker (pepper i samme fil) | **Delvis** | PIN og tvangskode er nå 8 sifre (100× mer arbeid). **Rest:** pepper og hash ligger i samme container; flytt pepperen til TPM/egen hemmelighet i produksjon (HANDOFF) |
| 7 | Middels | Testordet ble lagret i rå transkripsjon og kunne nå Lolas modell | **Rettet** | Testordet sjekkes før lagring og før noen modell, på alle linjer (Charlie, Lola, SMS, gjestechat, dør, mikrofon). `test_test_word_never_stored_or_forwarded` |
| 8 | Middels | Månedstaket for bestillinger kunne sprenges over et månedsskifte; re-sjekk og statusendring var ikke atomisk | **Rettet** | Bestillinger telles etter godkjenningstidspunkt (`approved_at`). Re-sjekk og compare-and-set skjer under én lås. `test_monthly_cap_across_month_boundary` |
| 9 | Middels | Loggen kunne skrives om av DB-superbruker (kjeden er nøkkelløs), WORM-eksporten var bare i minnet, og tjenester kunne skrive som andre aktører | **Rettet (delvis)** | Hver rad får `writer` fra databaserollen (kan ikke forfalskes). Loggens siste hash forankres hvert 5. minutt på kill switch-enheten, som bare godtar økende sekvens; `check_anchors` avslører en omskrevet kjede. `test_rewritten_chain_detected_by_device_anchors`, `test_pg_writer_is_taken_from_the_database_role`. **Rest:** WORM-lagringen er fortsatt simulert til S3 Object Lock er satt opp |
| 10 | Middels–lav | Lolas container nådde Postgres-serveren med Charlies data; `net_edge` har Internett | **Rettet (delvis)** | Lola har nå sin **egen Postgres-instans** (`db-guest`) og ingen nettvei til hoveddatabasen; worker når `db-guest` via et eget nett Lola ikke er på. **Rest:** `gateway`, `approvals` og `killswitch-sim` står på `net_edge`, som ikke kan være internt når porter publiseres. Gatewayen trenger uansett Twilio |
| 11 | Lav–middels | Rate-taket på `/chat/guest` kunne omgås med nye sesjons-ID-er | **Rettet** | Tak per klientadresse og et globalt tak. `test_guest_chat_rate_limit_per_client` |
| 12 | Lav | Ventende forslag overlevde et lease-brudd og kunne godkjennes etterpå | **Rettet** | `LeaseVerifier.active_since()`; godkjenning nektes og forslaget fryses hvis det har vært et brudd siden forslaget ble laget. `test_lease_gap_freezes_pending_proposals` |
| 13 | Lav | Den signerte konteksten kunne gjenbrukes i 5 minutter | **Rettet** | Levetid 120 s og maks 3 bruk per token (én tur). **Rest:** HMAC-nøkkelen deles av gateway og utfører |
| 14 | Lav | Lolas utgangsfilter var lett å omgå | **Forbedret** | Flere mønstre (godkjenn*, «koden til», tallrekker ≥ 4). Filteret er kosmetisk: Lola har ingen hemmeligheter å lekke |
| 15 | Lav | Godkjenningstjenesten: ubegrenset nonce-lager, uautentisert `GET /requests/{id}`, samme nøkkel for to tokentyper | **Rettet** | Nonce-lageret er begrenset og ryddes. `GET /requests/{id}` krever eierøkt. Tokenene har `typ` (`approval` / `owner_session`) og sjekkes |
| 16 | Lav | Webhooks kunne spilles av på nytt | **Rettet** | Identisk signert forespørsel godtas én gang (10 min vindu) |
| 17 | Lav | Ubrukt `charlie_agent`-innlogging med for vide rettigheter | **Rettet** | Ingen innlogging; lese- og loggrettigheter fjernet (migrasjon 0900) |
| 18 | Info | Tjenestelogger uten redigering; `/costs` og `/audit/verify` uautentisert; hemmelighetsfiler 644 i en 700-mappe | **Rettet / akseptert** | Redigeringsfilter på alle loggere; worker-endepunktene svarer bare lokalt (`docker compose exec`). Filrettighetene er nødvendige for at containerbrukeren skal kunne lese dem; mappen er 700 |

## Egne funn under byggingen (alle rettet)

| # | Funn | Rettelse |
|---|---|---|
| S1 | Lola delte nettverk med `executor` og `agents-charlie` via kill switch-nettet (funnet med live-probe) | Eget `net_ks_lola`; statisk test + probe i demoen |
| S2 | Logg-skrivere trengte SELECT på loggen for `INSERT … RETURNING` | `audit.append()` (security definer) |
| S3 | Heartbeat feilet stille (manglende rettighet) → falske varsler | Rettighet + migrasjon |
| S4 | Ukjent nummer ble registrert som samtale | Registreres ikke |
| S5 | Et simulatorendepunkt het «rotate-keys» og ble fanget av nivå 4-skanneren | Omdøpt; skanneren virker |
| S6 | Rate-tak telt dobbelt ved re-sjekk etter godkjenning | Rettet |
| S7 | Ingen uttrekk av samtaletranskripsjon → `les_samtale` tom, bare rå tekst lagret | `extract_transcripts` |
| S8 | Opptaks-URL fra webhook ble hentet uten vertssjekk (SSRF ved lekket nøkkel) | Bare `api.twilio.com` for egen konto, ingen redirects |
| S9 | PIN og tvangskode tok ulik tid å sjekke | Begge beregnes alltid |
| S10 | Hendelseskøen ville lagret `trust='duress'` | Lagres som `owner_pin` |

## Det som holdt

- Ingen nivå 4-vei: ingen håndterer, verktøy eller rute; Home Assistant bare med tillatte
  (domene, tjeneste)-par og entiteter.
- Utføreren godtar bare skjema-validerte `proposal/v1`; Lola-forslag avvises.
- Godkjenninger er bundet til forslag, parametre-hash, utfordring, metode og tekst-hash;
  engangs; regler sjekkes på nytt; handlingsnøkler er engangs og bundet til verktøy og parametre.
- Leaser: signatur, avvisning av gamle, feiler lukket.
- Lola-isolasjon (rute, rolle, tillit, egen nøkkel og eget tak).
- Tjeneste-til-tjeneste-autentisering feiler lukket.
- Security definer-funksjoner har fast `search_path`.
- Åpent verksted: ingen tale til tekst fra noen kilde.

## Restrisiko (samlet)

1. **Nummerforfalskning gir nivå 1–2.** Den som forfalsker eierens nummer kan lese kalender,
   notater og e-postoppsummeringer, og gjøre reversible ting. Bevisst etter kravet; kan strammes
   inn per verktøy.
2. **Utførerens container holder alle Charlie-nøkler.** Bruk leverandørenes egne tak og scopes.
3. **Pepper ved siden av PIN-hashen** (funn 6). Flytt til TPM/egen hemmelighet.
4. **Tvang kan sees i revisjonsloggen** (funn 3), men ikke av den som ringer.
5. **Oppsummeringer kan påvirke ordlyd** i svar til eieren (funn 5), aldri handlinger.
6. **Tilgjengelighet:** den som kjenner det hemmelige nummeret kan stoppe Charlie; gjenoppstart
   krever nøkkelbytte.
7. **Alt mot ekte tjenester og maskinvare er uverifisert** (se HANDOFF).

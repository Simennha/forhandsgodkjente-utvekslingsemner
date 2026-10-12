# HANDOFF

Status for en ny instans (eller et menneske) som tar over uten annen kontekst.
Sist oppdatert etter red team-gjennomgangen (se `docs/redteam-report.md`).

## Status per fase

| Fase | Status |
|---|---|
| 0 Fundament | Ferdig: repo, Makefile, Compose (10 tjenester + db + migrate), CI-skript, ruff, mypy, pre-commit, `policy/levels.yaml`, migrasjoner, adaptere med simulatorer |
| 1 Kjerne | Ferdig: telefon (simulator), identitet/PIN/tvangskode/testord, hendelseskø, regelmotor, Utfører med kortlivede nøkler, kalender, notater, uforanderlig logg, harde tak, kill switch komplett. `make demo` kjører scenarioet ende til ende over alle containere |
| 2 Lola | Ferdig: eget nummer, gjesteområde, én-veis kø (data, aldri ordre), eget tak og egen nøkkel, egen DB-rolle, eget nett, utgangsfilter |
| 3 Verksted | Ferdig: mikrofon-pipeline (simulert), Vanlig/Gjest/Åpent verksted, lager (ni tabeller, 45 artikler, R1–R11 + ESD), innkjøpsforslag per lokasjon, flytt på nivå 2 |
| 4 Notater og uttrekker | Ferdig: «husk dette», e-post via isolert uttrekker, ukentlig prosjektrapport, korpus (56 setninger), `make eval` med 0 harde brudd også med kompromittert modell |
| 5 Hus | Ferdig: Home Assistant-adapter, lys/varme/dørlås på nivå 3, alarm uten kodevei, månedstest |
| Red team | Ferdig: uavhengig gjennomgang med 18 funn (1 høy) + 10 egne. Alle høye/middels rettet eller delvis rettet med regresjonstester; restrisiko i `docs/redteam-report.md` |

## Kjøre

```bash
make install        # .venv med Python 3.12 (uv), alle avhengigheter
make test           # Postgres 16 i Docker + alle tester + 100 % dekning på killswitch/core.py
make lint typecheck # ruff + mypy --strict
make eval           # korpuset mot hele pipelinen (lydig + kompromittert mock-LLM)
make eval-live      # samme mot ekte Anthropic API (krever ANTHROPIC_API_KEY, koster penger)
make demo           # make secrets + docker compose up --build + demoscenario
make monthly-test   # månedlig drill i simulator
make demo-down      # stopp og slett demo-volumer
```

Bak TLS-inspiserende proxy (som i utviklingsmiljøet mitt): `EXTRA_CA_FILE=/sti/til/ca.pem make demo`.

Demoen spiller eierens telefon, passkey, andre enhet og utenforstående med **simulerte nøkler**
fra `secrets/sim-devices.json`. Alt mellom (tjenester, nettsegmentering, DB-roller, signaturer,
regler, logg) er den ekte koden.

## Hva er testet hvor (og hva er IKKE testet)

- Testet i simulator og mot ekte Postgres 16: all logikk, alle roller/RLS, alle tjenester i
  Docker Compose (helse, nettisolasjon, demo ende til ende, backup-gjenoppretting).
- **Ikke verifisert mot virkelig tjeneste:** alt i tabellen under. Ingen ekte telefon er ringt,
  ingen ekte e-post sendt, ingen maskinvare koblet til, ingen ekte LLM kalt.

## Simulert – må kobles til virkeligheten

| Tjeneste | Hva som er simulert / mangler | Nøkkel / hemmelighet | Hvor den hører hjemme |
|---|---|---|---|
| Twilio (Charlie-nr, Lola-nr) | `SimTelephony`; TwiML-flyten (`/twilio/*`) er skrevet, ikke testet live | Account SID + Auth Token | `secrets/executor.json` og `secrets/gateway.json` (`twilio.account_sid`, `twilio.auth_token`). Webhooks i Twilio: `https://<offentlig>/twilio/voice/charlie`, `/twilio/voice/lola`, status `/twilio/status` |
| Offentlig inngang for webhooks | Ikke satt opp | – | Cloudflare Tunnel e.l. til `gateway:8000`, bare for `/twilio/*`; sett `PUBLIC_BASE_URL` |
| Anthropic API (Charlie) | Mock-LLM | API-nøkkel i egen workspace med forbruksgrense | `secrets/executor.json` → `anthropic.charlie_api_key`; `llm.backend: anthropic` i `config.yaml` |
| Anthropic API (Lola) | Mock-LLM | Egen API-nøkkel, egen workspace, lav grense | `secrets/lola.json` → `anthropic.lola_api_key` |
| Anthropic Admin API (kill switch) | `SimRevoker` | Admin-nøkkel (`sk-ant-admin…`) + ID-ene til API-nøklene | Bare på kill switch-enheten |
| Gmail + Google Calendar | `MockEmail`, `MockCalendar` | OAuth-token (gmail.modify/compose, calendar.events); trenger fornying | `secrets/executor.json` → `google.access_token` (legg til refresh-flyt før bruk) |
| Home Assistant | `MockHomeAssistant` | Langtidstoken for en **ikke-admin**-bruker som bare ser de konfigurerte entitetene | `secrets/executor.json` → `homeassistant.url`, `homeassistant.token`; entiteter i `config.yaml` (`house:`) |
| Tale til tekst | `SimSTT` | – | Installer `faster-whisper` i gateway-imaget, bruk `FasterWhisperSTT` |
| Tekst til tale | `SimTTS` / Twilio `<Say>` | – | Installer Piper + norsk stemme, sett `PIPER_VOICE` |
| Passkey (WebAuthn) | Ed25519-simulert telefon | Registrer passkey; lagre credential public key | `approvals`: bytt `passkey`-verifikatoren til `WebAuthnVerifier` og server PWA-en over HTTPS på `rp_id` |
| Godkjenningsboks | Ed25519-simulator | Boksens offentlige nøkkel | `secrets/approvals.json` → `box.public_key` |
| Kill switch-enhet | `killswitch-sim`, `SimHAL` (`PiHAL` skrevet, ikke testet) | Lease-nøkkelpar, PIN-hash, SMS-kode-hash, andre enhets offentlige nøkkel, admin-nøkler | Enheten (`docs/killswitch.md`); serverne får bare `lease.public_key` |
| Ruter/brannmur | `SimEgress` | Token som kan aktivere én forhåndslaget regel | Bare på kill switch-enheten (`router.token`) |
| Push-varsler | `SimNotifier` | ntfy-token/emne | Koble `NtfyNotifier` inn i `services/wiring.py` |
| Objektlagring (WORM) | `MemoryObjectStore` i worker | S3-nøkler uten DeleteObject; bøtte med Object Lock (Compliance) | `secrets/worker.json` → `objectstore.*`; bruk `S3ObjectLockStore` |
| Leverandørbestillinger | `MockSupplierOrders` | – | Ingen leverandør-API valgt; bestillinger går i dag ingen steder |
| Mikrofonrelé, kameralyd | `SimMicRelay`, `SimCameraAudio` | – | `GpioMicRelay` (gpiozero) |
| Vekkeord | `SimWakeWord` | – | f.eks. openWakeWord lokalt |
| Værdata | Fast tekst | – | f.eks. MET Norway API |
| Supabase Vault | Filbasert `FileSecretStore` | – | `SupabaseVaultStore` finnes, ikke testet |

## Sjekkliste for første ekte oppstart

1. [ ] Mini-PC med Linux, Docker, diskkryptering, automatiske sikkerhetsoppdateringer.
2. [ ] WireGuard: bare din telefon og PC som peers. `gateway` (8080), `approvals` (8443) og
       `killswitch-sim` (8090) er bundet til 127.0.0.1 – eksponer dem bare via WireGuard.
3. [ ] Fjern `killswitch-sim` fra `docker-compose.yml` når den ekte enheten er i drift
       (og `KS_ALLOW_FIRST_BOOT`; den ekte enheten provisjoneres én gang).
4. [ ] `make install && make test` på maskinen (alt grønt).
4b. [ ] Flytt `identity.pepper` ut av `gateway.json` (TPM eller egen hemmelighet), se red team #6.
5. [ ] `make secrets` – les `secrets/dev-credentials.txt`, lær deg PIN, tvangskode og testord
       utenat, og **slett filen**. Slett `secrets/sim-devices.json` i produksjon.
6. [ ] Fyll inn ekte nøkler for hver rad i tabellen over, én tjeneste av gangen. Start med
       Anthropic (to separate workspaces med forbruksgrense), deretter Twilio.
7. [ ] Sett ekte numre i `config.yaml` (`identity:`), riktige HA-entiteter (`house:`) og
       `llm.backend: anthropic`.
8. [ ] Kjør `make eval-live` med et lite budsjett. Les avvikene (myke forventninger).
9. [ ] Monter og test kill switch-enheten etter `docs/killswitch.md`. Koble serverne til den ekte
       enhetens `lease.public_key` først når testprosedyren er grønn.
10. [ ] Registrer passkey på telefonen og bytt `approvals` til `WebAuthnVerifier`.
11. [ ] Sett opp objektlagring med Object Lock og bytt worker til `S3ObjectLockStore`.
12. [ ] Første uke: kjør med Lola av og bare deg som bruker.
13. [ ] Kjør den månedlige testen (`docs/runbook.md`) før verkstedet åpnes for andre.

## Hvor ting ligger

- `policy/levels.yaml` – nivåer per verktøy (eneste sannhetskilde).
- `config.example.yaml` – alle tak, vinduer, numre og modell-ID-er.
- `src/assistants/` – se `docs/architecture.md` for kart.
- `supabase/migrations/`, `supabase/seed/` – skjema, roller, RLS, logg, lager, seed.
- `tests/` – testene; `tests/adversarial/corpus.yaml` – angrepskorpuset.
- `docs/` – arkitektur, trusselmodell, kill switch, runbook, red team-rapport.
- `DECISIONS.md` – alle valg tatt uten å spørre.

## Hvis du skal fortsette utviklingen

- Nytt verktøy: legg til i `policy/levels.yaml`, skjema i `executor/schemas.py`, håndterer i
  `executor/handlers.py`. `tests/test_tool_registry.py` feiler til alle tre finnes.
- Aldri legg til noe som ligner nivå 4: `tests/test_level4_absent.py` skanner koden.
- Hold `rules/engine.py` under 600 linjer og uten I/O.
- GitHub-push var blokkert i byggeøkten (403); alle commits ligger lokalt på grenen
  `claude/charlie-lola-build-kalspv` til push er gjort.

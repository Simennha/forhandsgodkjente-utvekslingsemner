# HANDOFF

Status for en ny instans (eller et menneske) som tar over uten annen kontekst.

## Status per fase

| Fase | Status |
|---|---|
| 0 Fundament | Ferdig: repo, Makefile, Compose (10 tjenester + db + migrate), CI-skript, ruff, mypy, pre-commit, `policy/levels.yaml`, migrasjoner, adaptere med simulatorer, kill switch-kjerne med 100 % dekning |
| 1 Kjerne | Ferdig: telefon (simulator), identitet/PIN/tvangskode/testord, hendelseskø, regelmotor, Utfører med kortlivede nøkler, kalender, notater, uforanderlig logg, harde tak, kill switch komplett (kjerne, enhet, lease, gjenoppstart). `make demo` kjører scenarioet ende til ende over alle containere |
| 2 Lola | Ferdig: eget nummer, gjesteområde (`guest`-skjema), én-veis kø (data, aldri ordre), eget tak og egen nøkkel, egen DB-rolle uten tilgang til Charlie, eget nett uten rute til Charlie-siden, utgangsfilter |
| 3 Verksted | Ikke startet |
| 4 Notater og uttrekker | Ikke startet |
| 5 Hus | Ikke startet |

## Kjøre

Demoen (`make demo`) spiller telefon, passkey og andre enhet med simulerte nøkler fra
`secrets/sim-devices.json`. Alt mellom (tjenester, nett, roller, signaturer) er ekte kode.


```bash
make install        # .venv med Python 3.12
make test           # starter Postgres 16 i Docker, kjører alt, krever 100 % dekning på killswitch/core.py
make lint typecheck
make demo           # make secrets + docker compose up --build + vent på friske tjenester
```

Bak TLS-inspiserende proxy: `EXTRA_CA_FILE=/sti/til/ca.pem make demo`.

## Hvor ting ligger

- `policy/levels.yaml` – nivåer per verktøy (eneste sannhetskilde).
- `src/assistants/killswitch/core.py` – ren tilstandsmaskin.
- `src/assistants/adapters/` – alle eksterne tjenester (ekte + simulator).
- `supabase/migrations/` – skjema, roller, RLS, logg, lager.

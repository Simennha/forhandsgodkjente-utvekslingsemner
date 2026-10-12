# Charlie og Lola

To AI-assistenter for et robotikk- og ingeniørverksted (Norge ~300 m², Sveits ~160 m²):

- **Charlie** – eierens private assistent. Telefon, SMS, chat og verkstedmikrofon. Kalender,
  e-post, lager, prosjekter, sensorer og hus. Kan handle innenfor strenge grenser.
- **Lola** – resepsjon for alle andre (gjester, leverandører, venner i åpent verksted). Eget
  nummer, egne nøkler, eget tak. Ser bare gjesteområdet og har en én-veis kø til Charlie.

**Hovedprinsipp:** sikkerheten sitter i vanlig kode utenfor modellen. Modellene foreslår;
koden bestemmer. Alt er testet også med en bevisst *kompromittert* modell.

## Tilgangsnivåer

| Nivå | Betyr | Eksempler | Krav |
|---|---|---|---|
| 1 | Les | lager, sensorer, kalender, e-post (som uttrekk) | kjent nummer |
| 2 | Gjør alene, reversibelt | egne kalenderhendelser, e-postutkast, notater, verkstedlys, flytt på lager | kjent nummer |
| 3 | Foreslå og bekreft | sende e-post, bestille (≤ 5 000 kr), invitasjoner, huslys, varme, dørlås | nummer + PIN, deretter passkey (godkjenningsboks over 5 000 kr) |
| 4 | Aldri | slette backup, endre nøkler/brannmur, starte maskiner, flytte penger, slå av alarm | finnes ikke i koden |

Kilden er `policy/levels.yaml`. Tak og tider står i `config.example.yaml`.

## Kom i gang

Krav: Linux eller macOS, Docker med Compose, [uv](https://docs.astral.sh/uv/) (eller Python 3.12).

```bash
make install        # .venv med Python 3.12 og alle avhengigheter
make test           # alle tester; starter Postgres 16 i Docker (port 54329)
make lint typecheck # ruff og mypy --strict
make eval           # angrepskorpuset (56 setninger) mot hele pipelinen, med mock-LLM
make demo           # lokale hemmeligheter + alle tjenester + demoscenario ende til ende
make monthly-test   # månedlig drill: varsler, backup, tvangskode, kill switch (simulator)
make demo-down      # stopp demoen og slett volumene
```

`make eval-live` kjører korpuset mot ekte Anthropic API (krever `ANTHROPIC_API_KEY`, koster penger).

### Demoen

`make demo` bygger ett image, starter `db`, `db-guest`, `migrate` og de ti tjenestene (`gateway`, `rules`,
`executor`, `agents-charlie`, `agents-lola`, `extractor`, `workroom`, `approvals`, `worker`,
`killswitch-sim`) og kjører `scripts/demo.py`:

1. helse og nettsegmentering (Lola når ikke Charlie-siden eller hoveddatabasen),
   eieren logger inn (simulert passkey) og setter Vanlig modus (oppstart = mikrofoner av),
2. samtale inn fra eierens nummer, samtykkemelding,
3. nivå 1-svar (klokke, lager fra Postgres),
4. PIN via DTMF,
5. nivå 3-forslag (e-post),
6. godkjenning med (simulert) passkey og utføring, replay avvist,
7. hash-kjedet logg verifisert, sletting blokkert,
8. Lola svarer gjester og avslører ingenting om Charlie,
9. kill switch: ett ring fra kjent nummer → PAUSED,
10. manuell gjenoppstart (PIN + andre enhet + logg + nøkkelbytte) → ACTIVE,
11. kill switch-containeren startes på nytt i PAUSED og forblir i PAUSED.

Telefon, passkey, andre enhet og leverandører er **simulert**. Se `HANDOFF.md` for hva som må
kobles til virkeligheten.

## Dokumentasjon

- `docs/architecture.md` – tjenester, nett, dataflyt, diagram
- `docs/threat-model.md` – STRIDE per lag og konkrete scenarioer
- `docs/killswitch.md` – nødstopp: logikk, innkjøp, montering, testprosedyre, gjenoppstart
- `docs/runbook.md` – daglig, ukentlig, månedlig og kvartalsvis drift
- `docs/redteam-report.md` – funn, rettelser og restrisiko
- `DECISIONS.md` – alle valg som ble tatt uten å spørre
- `HANDOFF.md` – status, simulert vs. ekte, sjekkliste for første oppstart

# Kill switch

Uavhengig nødstopp for Charlie (og Lola). Egen enhet, eget batteri, egen strøm og eget
mobilnett. Logikken er en ren tilstandsmaskin (`src/assistants/killswitch/core.py`, 100 %
linje- og grendekning og property-tester). Driveren (`driver.py`) og maskinvarelaget
(`pi_hal.py`, simulator i `SimHAL`) er atskilt fra logikken.

> **Status:** Logikk, driver og server-side håndhevelse er testet i simulator. Maskinvaren
> (`pi_hal.py`) er skrevet etter standard AT-kommandoer og gpiozero, men **ikke verifisert
> på en ekte enhet**.

## Tilstander og regler

| Fra | Utløser | Til | Effekter |
|---|---|---|---|
| ACTIVE | 2 ring fra samme ukjente nummer innen 2 min | PAUSED | Charlie stoppes, ventende forslag fryses, eier varsles |
| ACTIVE | 1 ring fra kjent nummer | PAUSED | som over |
| ACTIVE/PAUSED | 3. ring (alle numre) innen 10 min | STOPPED | + Claude-nøkler og verktøynøkler trekkes, servernettet mister Internett |
| ACTIVE/PAUSED | SMS med riktig kode (fra hvilket som helst nummer) | STOPPED | som over |
| ACTIVE/PAUSED | Fysisk bryter ved inngangen (virker uten nett) | STOPPED (konfig) | som over |
| ACTIVE/PAUSED | Knapp i appen (via VPN) | PAUSED eller STOPPED | som over |
| ACTIVE/PAUSED | Uvanlig aktivitet i loggen (regler i konfig) | PAUSED (konfig) | som over |
| hvilken som helst | Tvangskode brukt mot Charlie | uendret | Stille alarm: SMS + push til andre nummer |
| hvilken som helst | Lola av/på | uendret for Charlie | Bare Lola stoppes/startes |
| PAUSED/STOPPED | Gjenoppstart med PIN **og** annen enhet **og** logg gjennomgått **og** nøkler byttet | ACTIVE | Charlie kan igjen; frosne forslag forblir frosne |

Alle tider og antall ligger i `config.yaml` under `killswitch:`. Tilstanden kan bare øke
(ACTIVE → PAUSED → STOPPED). **Ingen kodevei gir ACTIVE uten alle fire betingelsene**
(`tests/killswitch/test_properties.py::test_active_only_reachable_through_full_resume`).
Alarm, brannvarsling og maskinenes nødstopp er egne kretser og påvirkes ikke; det finnes
ingen effekt i koden som kan nå dem.

### Tilstand overlever omstart

Tilstand, ringehistorikk og lease-teller skrives atomisk til enhetens egen lagring (`FileDeviceState`)
**før** en ny lease sendes. Ved oppstart:

- lagret tilstand finnes → den gjenopprettes (en STOPPED-enhet starter som STOPPED),
- ingen lagret tilstand → bare godtatt ved provisjonering (første oppstart); ellers STOPPED,
- ulesbar tilstand → STOPPED.

Lease-telleren er tidsbasert og øker alltid, så serverne godtar nye leaser etter omstart uten å
kunne lures av gamle.

### Forankring av revisjonsloggen

Worker sender loggens siste (sekvens, hash) til enheten hvert 5. minutt. Enheten godtar bare
økende sekvens og lagrer de siste 2000. `check_anchors` sammenligner dem med databasen: en kjede
som er skrevet om og hashet på nytt (f.eks. av en databasesuperbruker) avsløres.

### Fail-closed lease

Enheten signerer `{tilstand, lola, sekvens, utstedt, utløper}` med en Ed25519-nøkkel som bare
finnes på enheten, og sender den til `gateway`, `executor`, `agents-charlie` og `agents-lola`
hvert 5. sekund (levetid 30 s). Uten gyldig, fersk ACTIVE-lease regnes Charlie som PAUSED.
Eldre leaser avvises (stigende sekvens). Kutter noen nettet eller strømmen til enheten, stopper
Charlie av seg selv innen 30 sekunder.

## Innkjøpsliste

| Del | Eksempel | Merknad |
|---|---|---|
| Datamaskin | Raspberry Pi Zero 2 W | Wi-Fi og Bluetooth slås av i `config.txt` (`dtoverlay=disable-wifi`, `disable-bt`) |
| Mobilmodem | SIM7600E-H 4G HAT (Waveshare) eller tilsvarende | Eget SIM-kort med **hemmelig nummer**, egen operatør gjerne forskjellig fra hovedlinjen |
| Strøm | UPS-HAT for Pi Zero med 18650/LiPo + egen kurs/stikkontakt | Skal tåle 4+ timer uten nettstrøm |
| Bryter | Låsende soppbryter eller nøkkelbryter, NO-kontakt | Ved inngangen, merket «Charlie nødstopp» |
| LED | Rød LED + 330 Ω | Av = ACTIVE, blinker = PAUSED, fast = STOPPED |
| Nettverk | USB-Ethernet-adapter til egen VLAN-port (`ks`) | Bare til lease-leveranse mot serveren |
| Kabinett | Låsbart, ikke-brennbart | Plombert; åpning skal synes |

## Montering

1. **Pi og modem:** Monter SIM-HAT på Pi Zero 2 W (40-pinners header). Sett inn SIM-kort med
   PIN-kode slått av (SIM-PIN håndteres ikke av driveren). Antenne utenfor kabinettet.
2. **Bryter:** Den ene polen til GPIO17 (pinne 11), den andre til GND (pinne 9). Driveren bruker
   intern pull-up; lukket kontakt = STOPP.
3. **LED:** GPIO27 (pinne 13) → 330 Ω → LED → GND.
4. **Strøm:** UPS-HAT mellom strømforsyning og Pi. Strømforsyningen på **egen kurs** (ikke samme
   kurs som mini-PC-en).
5. **Nett:** USB-Ethernet til en egen VLAN-port på ruteren. Brannmur: bare enheten → server
   TCP 8000 på `/internal/lease`-endepunktene. Ingen vei inn til enheten fra serveren.
6. **Programvare:** Raspberry Pi OS Lite. Installer repoet som egen bruker, kun pakkene
   `assistants` + `gpiozero` + `pyserial`. Kjør `KillSwitchDevice` med `PiHAL` som systemd-tjeneste
   med `Restart=always`. Les-kun rotfilsystem (`overlayroot`) anbefales.
7. **Hemmeligheter på enheten (og bare der):** lease-privatnøkkel, PIN-hash, SMS-kode-hash,
   offentlig nøkkel til den andre enheten, Anthropic **Admin**-nøkkel (bare for å sette API-
   nøkler inaktive), Twilio API-nøkkel med rett til å slette API-nøkler, token til ruterregelen.
8. **Ruteren:** Lag på forhånd en deaktivert regel «blokker server-VLAN mot Internett». Enheten
   får et token som bare kan *aktivere* denne regelen.

## Tilbaketrekking av nøkler ved STOPPED

| Leverandør | Hvordan | Adapter |
|---|---|---|
| Anthropic | Admin API: `api_keys.update(id, status="inactive")` for Charlies og Lolas nøkler | `AnthropicKeyRevoker` |
| Twilio | `DELETE /2010-04-01/Accounts/{sid}/Keys/{key_sid}` | `HttpRevoker` |
| Google | `POST https://oauth2.googleapis.com/revoke?token=...` | `HttpRevoker` |
| Home Assistant | Utgående nett kuttes; langtidstoken slettes for hånd ved gjenoppstart | – |
| Server-nett | Ruterregel aktiveres | `RouterApiEgress` |

## Testprosedyre (ved montering og hver måned)

Gjør testen med en tredje person til stede. Noter resultatet i driftsloggen.

1. **Utgangspunkt:** LED av. `make demo` eller ekte system: Charlie svarer.
2. **Ett ring fra kjent nummer:** Ring det hemmelige nummeret fra eget nummer. Forvent: enheten
   legger på, LED blinker, Charlie sier «ikke tilgjengelig», du får SMS og push.
3. **Gjenoppstart:** Følg prosedyren under. Forvent ACTIVE.
4. **Ukjent nummer:** Ring to ganger innen 2 minutter fra et lånt nummer. Forvent PAUSED.
   Ring én gang fra et annet ukjent nummer etter 3 minutter: forvent ingen endring.
5. **Tredje ring / SMS-kode:** (bare i testmiljø, eller med testnøkler) Forvent STOPPED,
   nøklene inaktive hos leverandørene, serveren uten Internett.
6. **Fysisk bryter:** Slå inn bryteren med nettverkskabelen trukket ut av enheten. Forvent
   STOPPED og fast LED, og at Charlie stopper innen 30 s (utløpt lease).
7. **Strømbrudd:** Trekk enhetens strøm. Forvent at enheten går på batteri og fortsetter.
8. **Nettbrudd:** Trekk enhetens nettverkskabel. Forvent at Charlie blir PAUSED innen 30 s, og
   at Charlie **ikke** kommer tilbake av seg selv før leasen er tilbake (og at frosne forslag
   forblir frosne).
9. **Tvangskode:** Ring Charlie, tast tvangskoden. Forvent normalt svar, bare harmløse svar, og
   stille alarm på det andre nummeret.
10. **Kontroll:** Bekreft at alarm, brannvarsling og maskinenes nødstopp ikke er påvirket.

`make monthly-test` kjører trinn 2–3 og 9 i simulator, pluss varsler og backup.

## Gjenoppstart (bare manuelt)

1. Gå gjennom loggen: `GET /audit/verify` på worker (eller `select * from audit.verify_chain()`),
   og les hendelsene siden stoppet. Noter hash-en til siste rad.
2. Bytt nøkler hos leverandørene (nye API-nøkler, nytt Twilio-nøkkelpar, nytt Google-token, nytt
   HA-token). Legg dem inn i hemmelighetslageret for `executor`/`agents-lola`.
3. På den lokale gjenoppstartsiden (over VPN): be om engangs-nonce.
4. På **den andre enheten**: signer `resume|<nonce>|<loggens siste hash>`.
5. Send PIN, nonce, hash og signatur. Enheten sjekker PIN, signaturen, at hash-en er loggens
   faktiske siste hash, og at alle aktive nøkler er forskjellige fra dem som var aktive da
   hendelsen startet. Bare da blir tilstanden ACTIVE.
6. Ruterregelen deaktiveres for hånd.

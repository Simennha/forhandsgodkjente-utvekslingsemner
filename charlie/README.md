# Charlie og Lola

To AI-assistenter for et robotikkverksted (Norge og Sveits):

- **Charlie** – eierens private assistent. Telefon, chat, kalender, lager, prosjekter, sensorer.
  Kan handle innenfor strenge grenser.
- **Lola** – resepsjon for alle andre. Eget nummer, egne nøkler, egne tak. Ser bare gjesteområdet.

**Hovedprinsipp:** sikkerheten sitter i vanlig kode utenfor modellen. En modell kan ikke
overtales til mer tilgang, fordi tilgangen aldri er modellens å gi.

Se `docs/architecture.md` for oversikt (fylles ut per fase).

## Kom i gang

```bash
make install     # Python 3.12-venv med alle avhengigheter
make test        # alle tester (starter Postgres 16 i Docker)
make lint typecheck
make demo        # genererer lokale hemmeligheter, starter alle tjenester, kjører demoen
make demo-down
```

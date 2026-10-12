-- Guest area content: the ONLY information Lola has. Idempotent.
insert into guest.info (key, title, body) values
  ('apningstider', 'Åpningstider', 'Åpent verksted torsdager 17-21 og lørdager 11-16. Ellers etter avtale.'),
  ('husregler', 'Husregler', 'Vernebriller ved alle maskiner. Ingen bruker maskiner alene. Spør før du bruker CNC, laser og dreiebenk. Rydd plassen din.'),
  ('adkomst', 'Adkomst', 'Inngang fra baksiden. Ring på dørtelefonen, så svarer resepsjonen.'),
  ('sikkerhet', 'Sikkerhet', 'Nødstopp er merket med rødt. Brannslukker ved døra og ved loddebordet. Lithiumbatterier lades bare i brannsikkert skap.'),
  ('wifi', 'Gjestenett', 'Nettet heter Verksted-Gjest. Passordet står på oppslaget ved døra.')
on conflict (key) do nothing;

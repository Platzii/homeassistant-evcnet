# 50five/EVC-net email-2FA — installatie en onderhoud

Deze kandidaatversie (`1.0.2-beta.1`, branch `feature/50five-email-2fa`) is gebaseerd op
`Platzii/homeassistant-evcnet` commit `3752d7c40cef99434e266f6d06aee2e6ecee5741` (1.0.1).
De fork staat op https://github.com/mcostantini70/homeassistant-evcnet.
Alle 53 tests, de gerichte Ruff-lint en de Python-syntaxcontrole zijn geslaagd.
De patch is met gesimuleerde HTTP-responses en Home Assistant 2026.2.3 getest.
Een echte 50five-login en echte laadpaal zijn nog niet getest. Er zijn geen echte
wachtwoorden, OTP's of sessies in de broncode of tests opgenomen.

## Installatie via een eigen fork (voorkeursroute)

1. Maak een volledige Home Assistant-back-up, inclusief configuratie en `.storage`.
2. Publiceer deze branch in je eigen GitHub-fork. Maak daar een release
   `v1.0.2-beta.1` met release-asset **`homeassistant-evcnet.zip`**.
   Alleen een tag is onvoldoende: `hacs.json` gebruikt `zip_release: true`.
   De aangepaste releaseworkflow kan dit in een fork uitvoeren; de oorspronkelijke
   beperking tot de upstream-eigenaar is verwijderd. Activeer zo nodig Actions
   in je fork. Publiceer pas nadat de tests slagen.
3. Zorg dat HACS uitsluitend jouw fork beheert voor het domein `evcnet`.
   Verwijder de upstream-download uit HACS voordat je de fork installeert.
   **Verwijder niet de EVC-net-configuratie onder Instellingen → Apparaten & diensten.**
   HACS kan bij verwijderen bestanden wissen; installeer daarom de fork vóór
   je Home Assistant opnieuw start.
4. HACS → menu → Custom repositories: voeg de URL van **jouw fork** toe,
   categorie **Integration**. Download release `v1.0.2-beta.1`; schakel zo nodig
   de weergave van prereleases/bètaversies in.
5. Herstart Home Assistant. De bestaande integratie vraagt bij de eerste
   ingebruikname om opnieuw te authenticeren. Open die melding, laat het
   wachtwoord leeg om het opgeslagen wachtwoord te gebruiken en bevestig.
6. Vul de zescijferige e-mailcode in, inclusief een eventuele nul aan het begin.
   Na succesvolle validatie herlaadt Home Assistant dezelfde configuratie.
7. Controleer laadpaalstatus en bestaande entity-ID's. Test `refresh_status`.
   Test start/stop alleen wanneer je de aangesloten auto daadwerkelijk wilt bedienen.
8. Herstart Home Assistant nogmaals. Bij een nog geldige sessie hoort er geen
   nieuwe OTP nodig te zijn. Controleer dat updates blijven binnenkomen.

Voor een nieuwe installatie voeg je EVC-net één keer toe onder Apparaten & diensten.
Gebruik voor Nederland `https://50five-snl.evc-net.com` en vul de OTP in wanneer
Home Assistant erom vraagt. Accounts zonder 2FA blijven ondersteund.

De map blijft `custom_components/evcnet`, het integratiedomein blijft `evcnet`,
en entity-ID-generatie, services en serviceparameters zijn niet gewijzigd.
De bestaande configuratie-entry, RFID-instellingen en opties blijven bij reauth behouden.
Je bestaande automatiseringen met `evcnet.start_charging`, `evcnet.stop_charging`
en `evcnet.refresh_status` hoeven daarom niet aangepast te worden.

## Sessies en fouten

- Login: `POST /Login/Login` met `emailField` en `passwordField`.
- Bij redirect naar `/2fa`: formulier ophalen en hidden `_token` uitlezen.
- OTP: multipart `POST /2fa_check` met `_token`, `_auth_code`, `VerifyOtp=Verify`.
- Succes vereist een redirect naar `/` of `/Overview`, een bereikbare dashboardpagina,
  `PHPSESSID` en een geldige `networkOverview`-respons via `/api/ajax`.
- Alle cookies worden accountgebonden bewaard in een privébestand
  `.storage/evcnet.session.<hash>`, inclusief PHPSESSID, SERVERID en eventuele
  aanvullende cookies. De opslag is niet versleuteld; behandel HA-back-ups als privé.
- `Max-Age` wordt bij ontvangst omgezet naar een absolute vervaldatum. Een herstart
  begint dus niet opnieuw een volledige geldigheidsperiode.
- Gewone API-calls verwerken cookievernieuwing en -verwijdering. Alleen gewijzigde
  cookiestatus wordt opnieuw opgeslagen; de sessie blijft ook tussen accounts gescheiden.
- Zonder bruikbare opgeslagen sessie start HA reauth. Er worden niet op de achtergrond
  telkens nieuwe e-mailcodes aangevraagd.
- Login-/2FA-redirects, 401/403 en herkenbare loginformulieren starten reauth.
  Een lege lijst wordt extra gecontroleerd via de dashboardpagina; een geldig
  ingelogd account zonder laadpunten mag werkelijk leeg zijn.
- Netwerkfouten en serverstoringen zijn aparte fouten. Een laadcommando wordt nooit
  automatisch herhaald na een loginfout.
- Een verkeerde OTP kan opnieuw worden ingevoerd. Bij een verlopen challenge start
  je authenticatie opnieuw om een nieuwe e-mailcode te vragen.
- Wachtwoord, OTP, CSRF-token, cookies en ruwe authenticatieresponses worden niet gelogd.
  OTP en CSRF-token worden niet opgeslagen. `secrets.yaml` wordt niet gewijzigd.

Rolling cookievernieuwing voorkomt geen eventuele absolute serverlimiet. Als de server
later alsnog de sessie beëindigt, vraagt Home Assistant opnieuw om authenticatie.

## Terug naar upstream

1. Maak opnieuw een volledige HA-back-up.
2. Vergelijk jouw fork met de gewenste officiële versie: OTP, sessieopslag,
   cookievernieuwing en reauth moeten alle vier ondersteund zijn.
3. Verwijder alleen de fork-download uit HACS; behoud de HA-integratieconfiguratie.
4. Voeg/download de officiële repository in HACS en herstart HA pas daarna.
5. Controleer bestaande entity-ID's, configuratie, RFID-opties en services.
   De officiële versie kan een andere cookieopslag gebruiken en één nieuwe login vragen.

## Ontwikkelen en publiceren

De releaseworkflow is geschikt gemaakt voor forks. Zet deze branch in de fork,
voer de testworkflow uit en publiceer een tag/release. Verander het integratiedomein
niet. Houd de authenticatiepatch als aparte commit om toekomstige upstream-updates
te kunnen vergelijken of samenvoegen.

Testen op Linux met Python 3.13:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
pytest -q
ruff check custom_components/evcnet/api.py custom_components/evcnet/config_flow.py custom_components/evcnet/coordinator.py custom_components/evcnet/session.py custom_components/evcnet/__init__.py tests
```

De HTTP-tests draaien uitsluitend tegen een lokale nepserver. HA-tests gebruiken
Home Assistant-klassen met gemockte serverclients en configuratiebeheer; dit is
geen volledige draaiende Home Assistant-installatie en geen live servervalidatie.
Op macOS zijn voor de lokale testomgeving HA's hardware-afhankelijkheden overgeslagen;
de relevante softwareafhankelijkheden zijn wel geïnstalleerd.

Bronnen:
- [Upstream](https://github.com/Platzii/homeassistant-evcnet)
- [HA config flow en reauth](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [HA authenticatiefouten](https://developers.home-assistant.io/docs/integration_setup_failures/)
- [HACS custom repositories](https://www.hacs.dev/docs/faq/custom_repositories/)
- [HACS releasebestanden](https://hacs.dev/docs/publish/start/)

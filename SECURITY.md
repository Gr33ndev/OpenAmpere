# Sicherheit

OpenAmpere kann Einstellungen eines Wechselrichters ändern. Sicherheitslücken nehmen wir deshalb ernst.

## Lücke melden

Bitte **nicht** als öffentliches Issue melden. Nutze stattdessen die private Meldung über GitHub:
**Security → Report a vulnerability** im Repository. Beschreibe, was betroffen ist und wie man es nachvollziehen kann.

Wir melden uns so bald wie möglich, in der Regel innerhalb einer Woche. Bitte gib uns Zeit für eine Korrektur, bevor
du Details veröffentlichst.

## Was OpenAmpere absichert

- Ansehen im Heimnetz ist ohne Anmeldung möglich. Ändern, Steuern und Datensicherung brauchen ein Passwort.
- Schutz gegen fremde Webseiten (CSRF-Header, Origin-Prüfung) und gegen DNS-Rebinding (Host-Prüfung).
- Steuerfunktionen sind ab Werk aus und starten im Testmodus.

## Was OpenAmpere nicht absichert

- OpenAmpere spricht kein HTTPS und ist nicht für den Betrieb im offenen Internet gedacht. Zugriff von unterwegs
  nur über ein VPN, **nie per Portfreigabe**.
- Modbus TCP selbst hat keine Anmeldung. Wer im Heimnetz ist, kann den Wechselrichter auch ohne OpenAmpere ansprechen.

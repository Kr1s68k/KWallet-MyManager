# KWallet-MyManager

Schlanker Ersatz für den KWalletManager: Einträge der KDE-Wallet ansehen, kopieren,
bearbeiten, umbenennen, anlegen und löschen. Gebaut mit Python und GTK 4, spricht
`kwalletd6` direkt über D-Bus an. Qt-Bindungen braucht es nicht.

Entstanden auf SteamOS (Steam Machine), läuft aber auf jedem Linux mit KDE Plasma 6.

## Funktionen

- Links alle Einträge nach Ordnern, rechts Anzeige und Bearbeitung
- Suche über alle Ordner
- Passwörter kopieren: Die Zwischenablage wird nach 30 Sekunden geleert
- Warnung bei Einträgen, die Programmen gehören (z. B. `Chromium Keys`)
- **Vor jeder Änderung eine verschlüsselte Komplettsicherung.** Scheitert die Sicherung,
  ändert die App nichts

## Voraussetzungen

- Python 3 mit PyGObject und GTK 4
- KDE Wallet (`kwalletd6`)
- GnuPG (`gpg`). Ein pinentry-Programm ist nicht nötig, die App fragt die Passphrase selbst ab

## Installation

```bash
git clone <repo-url> KWallet-MyManager
cd KWallet-MyManager
./install.sh
```

`install.sh` prüft die Voraussetzungen und legt einen Starter im Anwendungsmenü an, nur
für den aktuellen Benutzer und ohne root. Wurde der Ordner verschoben, einfach erneut
ausführen. Entfernen mit `./install.sh --remove`.

## Sicherungen

Die Sicherungen liegen in `~/.local/share/kwallet-mymanager/backups/` als
`JJJJMMTT-HHMMSS-anlass.json.gpg`. Die letzten 20 bleiben erhalten.

- Verschlüsselt wird mit einem eigenen GPG-Schlüsselpaar. Zum **Sichern** genügt der
  öffentliche Schlüssel, dafür braucht es keine Passphrase.
- Erst das **Zurückspielen** fragt nach der Passphrase.
- Der Klartext landet nie auf der Platte, er geht per Pipe an `gpg`.

### Erster Start

Ist noch kein Sicherungsschlüssel eingerichtet, fragt die App:

- **Schlüssel importieren …:** einen vorhandenen, exportierten Schlüssel (`.asc`)
  auswählen. Empfohlen für jede weitere Maschine, dann öffnet eine Passphrase die
  Sicherungen aller Geräte.
- **Neuen Schlüssel anlegen:** Passphrase festlegen, danach den Schlüssel gleich
  exportieren.
- **Später:** Die App läuft, ändert aber nichts, solange es keinen Schlüssel gibt.

Welcher Schlüssel gilt, steht in `~/.config/kwallet-mymanager/config.json`.

> **Wichtig:** Ohne privaten Schlüssel und Passphrase ist keine Sicherung lesbar. Den
> Export (Menü „Sicherungsschlüssel exportieren …“) zusammen mit der Passphrase an einem
> sicheren Ort aufbewahren, z. B. als Anhang in einem Passwort-Manager, und die Datei
> danach löschen.

## Dateien

| Datei | Inhalt |
|---|---|
| `app.py` | Oberfläche (GTK 4) |
| `kwallet.py` | Zugriff auf `kwalletd6` über D-Bus, Umwandlung der Map-Einträge |
| `backups.py` | Sichern, Aufräumen, Wiederherstellen |
| `gpgkey.py` | GPG-Aufrufe, Einrichtung und Konfiguration des Sicherungsschlüssels |
| `install.sh` | Starter im Anwendungsmenü anlegen oder entfernen |

## Bekannte Grenzen

- Ordner lassen sich nicht löschen, nur Einträge. Das ist Absicht.
- Ungespeicherte Änderungen gehen beim Wechsel zu einem anderen Eintrag ohne Nachfrage
  verloren.
- Geöffnet wird nur die Standard-Wallet (`networkWallet`, meist `kdewallet`).

## Lizenz

MIT, siehe [LICENSE](LICENSE).

---

by Kr1s68k + Claude

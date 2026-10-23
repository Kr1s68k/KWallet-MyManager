"""Sicherungen der Wallet vor jeder Aenderung.

Eine Sicherung ist der komplette Wallet-Inhalt als JSON, verschluesselt mit
GPG an den eingerichteten Sicherungsschluessel (*.json.gpg, siehe gpgkey.py).
Sichern braucht darum keine Passphrase, erst das Zurueckspielen.
Der Klartext landet nie auf der Platte: er geht per Pipe an gpg.
Alte Klartext-Sicherungen (*.json) bleiben lesbar.
"""
import base64
import json
import os
from datetime import datetime
from pathlib import Path

import gpgkey
from kwallet import TYPE_MAP, TYPE_PASSWORD

BACKUP_DIR = Path.home() / ".local/share/kwallet-mymanager/backups"
KEEP_BACKUPS = 20  # die neuesten 20 bleiben, aeltere werden geloescht
SUFFIX = ".json.gpg"


def needs_passphrase(path):
    return str(path).endswith(".gpg")


def _encode(kind, value):
    if kind in (TYPE_PASSWORD, TYPE_MAP):
        return {"type": kind, "value": value}
    return {"type": kind, "b64": base64.b64encode(value).decode("ascii")}


def _decode(item):
    if "b64" in item:
        return item["type"], base64.b64decode(item["b64"])
    return item["type"], item["value"]


def list_backups():
    """Alle Sicherungen, neueste zuerst."""
    if not BACKUP_DIR.is_dir():
        return []
    return sorted([*BACKUP_DIR.glob("*" + SUFFIX), *BACKUP_DIR.glob("*.json")],
                  key=lambda p: p.name, reverse=True)


def prune_backups(backups):
    """Entscheidet, welche alten Sicherungen weg duerfen.

    backups  Liste von Path, neueste zuerst. Der Dateiname beginnt mit dem
             Zeitstempel, z. B. 20261023-171502-loeschen.json; das Datum
             liefert datetime.strptime(p.name[:15], "%Y%m%d-%H%M%S").
    Rueckgabe: Liste der Pfade, die geloescht werden sollen.

    Die neueste Sicherung darf nie dabei sein - sie ist gerade erst vor der
    laufenden Aenderung entstanden.
    """
    return backups[KEEP_BACKUPS:]


def save_backup(wallet, reason):
    """Sichert die ganze Wallet und gibt den Pfad zurueck. Scheitert das,
    wirft es - der Aufrufer bricht dann die Aenderung ab."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    data = {
        "wallet": wallet.name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "folders": {folder: {key: _encode(*entry) for key, entry in entries.items()}
                    for folder, entries in wallet.snapshot().items()},
    }
    slug = "".join(c if c.isalnum() else "-" for c in reason.lower())[:40].strip("-")
    path = BACKUP_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug or 'backup'}{SUFFIX}"
    blob = gpgkey.encrypt(json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    for old in prune_backups(list_backups()):
        if old != path:
            old.unlink(missing_ok=True)
    return path


def load_backup(path, passphrase=None):
    raw = Path(path).read_bytes()
    if needs_passphrase(path):
        raw = gpgkey.decrypt(raw, passphrase)
    data = json.loads(raw.decode("utf-8"))
    return {folder: {key: _decode(item) for key, item in entries.items()}
            for folder, entries in data["folders"].items()}, data


def restore(wallet, folders):
    """Spielt eine geladene Sicherung (folders aus load_backup) zurueck: legt
    fehlende Ordner/Eintraege an und ueberschreibt abweichende. Eintraege, die
    es heute zusaetzlich gibt, bleiben stehen. Gibt die Zahl der geschriebenen
    Eintraege zurueck. Nimmt die Daten statt des Pfads, damit nicht ein
    zweites Mal entschluesselt werden muss."""
    save_backup(wallet, "vor-wiederherstellung")
    existing = set(wallet.folders())
    written = 0
    for folder, entries in folders.items():
        if folder not in existing:
            wallet.create_folder(folder)
        for key, (kind, value) in entries.items():
            try:
                current = wallet.read(folder, key)
            except Exception:
                current = None
            if current != (kind, value):
                wallet.write(folder, key, kind, value)
                written += 1
    return written

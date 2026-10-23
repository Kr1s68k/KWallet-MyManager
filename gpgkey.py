"""Sicherungsschluessel: GPG-Aufrufe und Einrichtung.

Welcher Schluessel gilt, steht in ~/.config/kwallet-mymanager/config.json
(Fingerabdruck) - nicht im Code. So laeuft dasselbe Programm auf jeder
Maschine; die Einrichtung beim ersten Start legt den Schluessel an oder
importiert einen vorhandenen.

Passphrasen fragt die App selbst ab und reicht sie per eigener Pipe an gpg
(--pinentry-mode loopback --passphrase-fd). Nie auf der Kommandozeile, nie
in einer Datei, und ein pinentry-Programm wird nicht gebraucht.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

CONFIG_FILE = Path.home() / ".config/kwallet-mymanager/config.json"
KEY_UID = "KWallet-MyManager Sicherung <kwallet-mymanager@localhost>"


class KeyMissing(RuntimeError):
    """Kein Sicherungsschluessel eingerichtet oder nicht in ~/.gnupg."""


class BadPassphrase(RuntimeError):
    pass


# --- gpg aufrufen -------------------------------------------------------------

def gpg_installed():
    return shutil.which("gpg") is not None


def _gpg(args, data=b"", passphrase=None, timeout=60):
    """gpg im Batch-Modus. data geht auf stdin; eine Passphrase ueber eine
    zweite Pipe, deren Nummer gpg per --passphrase-fd erfaehrt."""
    cmd = ["gpg", "--quiet", "--batch", "--yes", "--status-fd", "2"]
    fds = ()
    if passphrase is not None:
        rfd, wfd = os.pipe()
        os.write(wfd, passphrase.encode("utf-8"))  # Passphrasen sind kurz, passt in die Pipe
        os.close(wfd)
        cmd += ["--pinentry-mode", "loopback", "--passphrase-fd", str(rfd)]
        fds = (rfd,)
    try:
        return subprocess.run(cmd + args, input=data, capture_output=True,
                              timeout=timeout, pass_fds=fds)
    finally:
        for fd in fds:
            os.close(fd)


def _status(proc, keyword):
    """Zeilen '[GNUPG:] KEYWORD ...' aus stderr, jeweils als Liste der Felder."""
    out = []
    for line in proc.stderr.decode(errors="replace").splitlines():
        parts = line.split()
        if parts[:2] == ["[GNUPG:]", keyword]:
            out.append(parts[2:])
    return out


def _errtext(proc):
    return "\n".join(line for line in proc.stderr.decode(errors="replace").splitlines()
                     if not line.startswith("[GNUPG:]")).strip()


# --- Konfiguration ----------------------------------------------------------

def configured_key():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["backup_key"]
    except (OSError, ValueError, KeyError):
        return None


def _save_config(fingerprint):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"backup_key": fingerprint}, indent=1) + "\n",
                           encoding="utf-8")


def _has_public(fpr):
    return _gpg(["--list-keys", fpr]).returncode == 0


def _has_secret(fpr):
    return _gpg(["--list-secret-keys", fpr]).returncode == 0


def ready():
    """True, wenn gesichert werden kann (Schluessel eingerichtet und vorhanden)."""
    fpr = configured_key()
    return bool(fpr) and gpg_installed() and _has_public(fpr)


def require_key():
    fpr = configured_key()
    if not gpg_installed():
        raise RuntimeError("Das Programm gpg ist nicht installiert.")
    if not fpr or not _has_public(fpr):
        raise KeyMissing("Kein Sicherungsschlüssel eingerichtet.")
    return fpr


# --- Einrichtung --------------------------------------------------------------

def create_key(passphrase):
    """Neues Schluesselpaar (ed25519/cv25519, ohne Ablaufdatum) anlegen und
    eintragen. Gibt den Fingerabdruck zurueck."""
    proc = _gpg(["--quick-gen-key", KEY_UID, "default", "default", "never"],
                passphrase=passphrase, timeout=120)
    created = _status(proc, "KEY_CREATED")
    if proc.returncode != 0 or not created:
        raise RuntimeError("Schlüssel anlegen fehlgeschlagen:\n" + _errtext(proc))
    fpr = created[0][1]
    _save_config(fpr)
    return fpr


def import_key(path):
    """Schluesseldatei importieren (Export des privaten Schluessels, z. B. aus
    KeePass) und den darin enthaltenen Verschluesselungs-Schluessel eintragen."""
    proc = _gpg(["--import"], Path(path).read_bytes(), timeout=120)
    fprs = {fields[1] for fields in _status(proc, "IMPORT_OK") if len(fields) > 1}
    for fpr in sorted(fprs):
        listing = _gpg(["--list-keys", "--with-colons", fpr]).stdout.decode()
        pub = next((l.split(":") for l in listing.splitlines() if l.startswith("pub:")), None)
        if pub and "E" in pub[11]:  # Feld 12: Faehigkeiten des ganzen Schluessels
            _save_config(fpr)
            return fpr, _has_secret(fpr)
    raise RuntimeError(f"In {path} wurde kein Verschlüsselungs-Schlüssel gefunden.\n"
                       + _errtext(proc))


def export_secret(passphrase):
    """Privaten Schluessel als ASCII-Text (fuer den KeePass-Anhang)."""
    proc = _gpg(["--armor", "--export-secret-keys", require_key()], passphrase=passphrase)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Export fehlgeschlagen:\n" + _errtext(proc))
    return proc.stdout


# --- Ver- und Entschluesseln ---------------------------------------------------

def encrypt(data):
    """Bytes -> verschluesselte Bytes; braucht nur den oeffentlichen Schluessel.
    Wirft bei jedem Fehler."""
    proc = _gpg(["--encrypt", "--recipient", require_key(), "--trust-model", "always"], data)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Verschlüsseln fehlgeschlagen:\n" + _errtext(proc))
    return proc.stdout


def decrypt(raw, passphrase):
    proc = _gpg(["--decrypt"], raw, passphrase=passphrase, timeout=120)
    if proc.returncode == 0:
        return proc.stdout
    # Bei falscher Passphrase meldet gpg 2.4 irrefuehrend auch NO_SECKEY; eindeutig
    # ist nur der Fehlercode (untere 16 Bit, 11 = GPG_ERR_BAD_PASSPHRASE).
    codes = {int(f[1]) & 0xFFFF for f in _status(proc, "ERROR")
             if len(f) > 1 and f[1].isdigit()}
    if 11 in codes or _status(proc, "BAD_PASSPHRASE"):
        raise BadPassphrase("Passphrase falsch.")
    if _status(proc, "NO_SECKEY"):
        raise KeyMissing("Der private Schlüssel zu dieser Sicherung fehlt in ~/.gnupg.")
    raise RuntimeError("Entschlüsseln fehlgeschlagen:\n" + _errtext(proc))

"""Zugriff auf KWallet (kwalletd6) ueber D-Bus.

Bewusst ohne Qt: die Qt-Bindungen fehlen auf SteamOS, Gio (GLib) ist da.
Map-Eintraege speichert KWallet als QDataStream-serialisierte
QMap<QString,QString>; die Umwandlung steht unten in decode_map/encode_map.
"""
import struct

from gi.repository import Gio, GLib

SERVICE = "org.kde.kwalletd6"
PATH = "/modules/kwalletd6"
IFACE = "org.kde.KWallet"
APPID = "KWallet-MyManager"

TYPE_UNKNOWN, TYPE_PASSWORD, TYPE_STREAM, TYPE_MAP = 0, 1, 2, 3
TYPE_NAMES = {TYPE_UNKNOWN: "Unbekannt", TYPE_PASSWORD: "Passwort",
              TYPE_STREAM: "Binärdaten", TYPE_MAP: "Zugangsdaten (Map)"}

# Ordner, die Programme fuer sich selbst anlegen. Wer dort aendert oder
# loescht, meldet das jeweilige Programm ab oder macht seine Daten unlesbar.
MANAGED_FOLDERS = {"xdg-desktop-portal", "Chromium Keys", "Claude Keys",
                   "Chrome Keys", "Brave Keys", "Cursor Keys", "Code Keys"}


def is_managed(folder):
    return folder in MANAGED_FOLDERS or folder.endswith(" Keys")


class WalletError(Exception):
    pass


# --- QMap<QString,QString> <-> bytes ---------------------------------------

_NULL = 0xFFFFFFFF


def _read_qstring(raw, pos):
    (length,) = struct.unpack_from(">I", raw, pos)
    pos += 4
    if length == _NULL:
        return "", pos
    return raw[pos:pos + length].decode("utf-16-be"), pos + length


def _write_qstring(text):
    data = text.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def decode_map(raw):
    raw = bytes(raw)
    if not raw:
        return {}
    (count,) = struct.unpack_from(">I", raw, 0)
    pos, result = 4, {}
    for _ in range(count):
        key, pos = _read_qstring(raw, pos)
        value, pos = _read_qstring(raw, pos)
        result[key] = value
    return result


def encode_map(mapping):
    out = struct.pack(">I", len(mapping))
    for key, value in mapping.items():
        out += _write_qstring(key) + _write_qstring(value)
    return out


# --- Wallet -----------------------------------------------------------------

class Wallet:
    def __init__(self, name=None):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = name or self._call("networkWallet", "()", (), "(s)")
        self.handle = -1

    def _call(self, method, sig, args, out, timeout=10_000):
        try:
            reply = self.bus.call_sync(
                SERVICE, PATH, IFACE, method, GLib.Variant(sig, args),
                GLib.VariantType(out), Gio.DBusCallFlags.NONE, timeout, None)
        except GLib.Error as err:
            raise WalletError(f"{method}: {err.message}") from err
        return reply.unpack()[0]

    def _h(self, method, sig, *args, out, timeout=10_000):
        """Aufruf mit Handle und App-ID; oeffnet die Wallet bei Bedarf neu
        (kwalletd schliesst sie nach Leerlauf oder beim Sperren)."""
        self.ensure_open()
        return self._call(method, sig, (self.handle, *args, APPID), out, timeout)

    # Oeffnen/Schliessen. open() kann auf eine Passwortabfrage warten.
    def ensure_open(self):
        if self.handle >= 0 and self._call("isOpen", "(i)", (self.handle,), "(b)"):
            return
        handle = self._call("open", "(sxs)", (self.name, 0, APPID), "(i)",
                            timeout=300_000)
        if handle < 0:
            raise WalletError("Wallet wurde nicht geöffnet (abgelehnt oder abgebrochen).")
        self.handle = handle

    def close(self):
        if self.handle >= 0:
            try:
                self._call("close", "(ibs)", (self.handle, False, APPID), "(i)")
            except WalletError:
                pass
            self.handle = -1

    # Lesen. kwalletd6 liefert Listen gelegentlich mehrfach - darum set().
    def folders(self):
        return sorted(set(self._h("folderList", "(is)", out="(as)")), key=str.lower)

    def entries(self, folder):
        return sorted(set(self._h("entryList", "(iss)", folder, out="(as)")),
                      key=str.lower)

    def entry_type(self, folder, key):
        return self._h("entryType", "(isss)", folder, key, out="(i)")

    def read(self, folder, key):
        """Gibt (typ, wert) zurueck: str, dict oder bytes je nach Typ."""
        kind = self.entry_type(folder, key)
        if kind == TYPE_PASSWORD:
            return kind, self._h("readPassword", "(isss)", folder, key, out="(s)")
        if kind == TYPE_MAP:
            return kind, decode_map(self._h("readMap", "(isss)", folder, key, out="(ay)"))
        return kind, bytes(self._h("readEntry", "(isss)", folder, key, out="(ay)"))

    # Schreiben. Rueckgabe 0 heisst Erfolg.
    def _check(self, rc, what):
        if rc != 0:
            raise WalletError(f"{what} fehlgeschlagen (Code {rc}).")

    def write(self, folder, key, kind, value):
        if kind == TYPE_PASSWORD:
            rc = self._h("writePassword", "(issss)", folder, key, value, out="(i)")
        elif kind == TYPE_MAP:
            rc = self._h("writeMap", "(issays)", folder, key, encode_map(value),
                         out="(i)")
        else:
            rc = self._h("writeEntry", "(issayis)", folder, key, bytes(value), kind,
                         out="(i)")
        self._check(rc, f"Schreiben von {folder}/{key}")

    def remove(self, folder, key):
        self._check(self._h("removeEntry", "(isss)", folder, key, out="(i)"),
                    f"Löschen von {folder}/{key}")

    def rename(self, folder, old, new):
        self._check(self._h("renameEntry", "(issss)", folder, old, new, out="(i)"),
                    f"Umbenennen von {folder}/{old}")

    def create_folder(self, folder):
        if not self._h("createFolder", "(iss)", folder, out="(b)"):
            raise WalletError(f"Ordner {folder} konnte nicht angelegt werden.")

    def snapshot(self):
        """Kompletter Inhalt: {ordner: {schluessel: (typ, wert)}}."""
        return {folder: {key: self.read(folder, key) for key in self.entries(folder)}
                for folder in self.folders()}

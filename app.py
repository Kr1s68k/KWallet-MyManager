#!/usr/bin/env python3
"""KWallet-MyManager - schlanker Ersatz fuer KWalletManager.

Links alle Eintraege nach Ordnern, rechts Anzeige und Bearbeitung.
Jede Aenderung legt vorher eine komplette Sicherung an (backups.py);
scheitert die Sicherung, wird nichts geaendert.
"""
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backups  # noqa: E402
import gpgkey  # noqa: E402
import kwallet as kw  # noqa: E402

APP_ID = "de.deck.KWalletMyManager"
CLIPBOARD_SECONDS = 30

CSS = """
.folder-header { font-weight: bold; padding: 10px 8px 4px 8px; opacity: 0.75; }
.managed-note  { color: #c27c0e; }
.entry-title   { font-size: 1.4em; font-weight: bold; }
.mono          { font-family: monospace; }
"""


def is_secret_field(name):
    name = name.lower()
    return any(word in name for word in ("pass", "secret", "token", "key", "pin"))


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app, wallet):
        super().__init__(application=app, title="KWallet-MyManager by Kr1s68k+Claude",
                         default_width=1050, default_height=680)
        self.wallet = wallet
        self.current = None            # (ordner, schluessel)
        self.editor = None             # Funktion, die den bearbeiteten Wert liefert

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Neuer Eintrag")
        add_btn.connect("clicked", lambda *_: self.dialog_new_entry())
        header.pack_start(add_btn)
        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Neu laden")
        reload_btn.connect("clicked", lambda *_: self.reload())
        header.pack_start(reload_btn)

        menu = Gio.Menu()
        menu.append("Jetzt sichern", "app.backup")
        menu.append("Sicherung wiederherstellen …", "app.restore")
        menu.append("Sicherungsordner öffnen", "app.open-backups")
        menu.append("Sicherungsschlüssel exportieren …", "app.export-key")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        # pack_end reiht von rechts nach links: Suche steht links neben dem Menü
        self.search = Gtk.SearchEntry(placeholder_text="Suchen …", width_chars=24)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        header.pack_end(self.search)

        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title.append(Gtk.Label(label="KWallet-MyManager", css_classes=["title"]))
        title.append(Gtk.Label(label="by Kr1s68k+Claude", css_classes=["subtitle"]))
        header.set_title_widget(title)

        # links: Liste
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.set_filter_func(self._filter)
        self.listbox.set_header_func(self._header)
        self.listbox.connect("row-selected", self._on_select)
        left = Gtk.ScrolledWindow(child=self.listbox, width_request=300)

        # rechts: Details + Statuszeile
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                              margin_top=18, margin_bottom=18,
                              margin_start=24, margin_end=24)
        self.status = Gtk.Label(xalign=0, wrap=True, margin_start=24, margin_end=24,
                                margin_bottom=10, css_classes=["dim-label"])
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.append(Gtk.ScrolledWindow(child=self.detail, vexpand=True))
        right.append(self.status)

        paned = Gtk.Paned(position=320, start_child=left, end_child=right)
        self.set_child(paned)
        self.reload()

    # ---------- Hilfen ----------

    def say(self, text):
        self.status.set_text(text)

    def error(self, message, detail=""):
        Gtk.AlertDialog(message=message, detail=str(detail)).show(self)

    def confirm(self, message, detail, action_label, on_yes):
        dialog = Gtk.AlertDialog(message=message, detail=detail,
                                 buttons=["Abbrechen", action_label],
                                 cancel_button=0, default_button=0)

        def done(dlg, result):
            try:
                if dlg.choose_finish(result) == 1:
                    on_yes()
            except GLib.Error:
                pass
        dialog.choose(self, None, done)

    def mutate(self, reason, action, done_text):
        """Sichern, dann aendern. Ohne Sicherung keine Aenderung."""
        try:
            path = backups.save_backup(self.wallet, reason)
        except gpgkey.KeyMissing:
            self.setup_key("Es wurde nichts geändert. Die Aktion bitte noch einmal "
                           "ausführen.")
            return False
        except Exception as err:  # noqa: BLE001
            self.error("Sicherung fehlgeschlagen – es wurde nichts geändert.", err)
            return False
        try:
            action()
        except Exception as err:  # noqa: BLE001
            self.error("Änderung fehlgeschlagen", f"{err}\n\nSicherung: {path}")
            return False
        self.say(f"{done_text}  ·  Sicherung: {path.name}")
        return True

    def copy_secret(self, text):
        clipboard = self.get_clipboard()
        clipboard.set(text)
        self.say(f"Kopiert – wird nach {CLIPBOARD_SECONDS} s aus der Zwischenablage entfernt.")

        def clear():
            def got(cb, result):
                try:
                    if cb.read_text_finish(result) == text:
                        cb.set("")
                except GLib.Error:
                    pass
            clipboard.read_text_async(None, got)
            return False
        GLib.timeout_add_seconds(CLIPBOARD_SECONDS, clear)

    # ---------- Liste ----------

    def reload(self, select=None):
        select = select or self.current
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        target = None
        try:
            for folder in self.wallet.folders():
                for key in self.wallet.entries(folder):
                    row = Gtk.ListBoxRow()
                    row.folder, row.key = folder, key
                    row.set_child(Gtk.Label(label=key, xalign=0, margin_start=16,
                                            margin_top=6, margin_bottom=6,
                                            ellipsize=3))
                    self.listbox.append(row)
                    if select == (folder, key):
                        target = row
        except kw.WalletError as err:
            self.error("Wallet konnte nicht gelesen werden", err)
        if target:
            self.listbox.select_row(target)
        else:
            self.current = None
            self.show_empty()

    def _filter(self, row):
        text = self.search.get_text().strip().lower()
        return not text or text in row.key.lower() or text in row.folder.lower()

    def _header(self, row, before):
        if before is not None and before.folder == row.folder:
            row.set_header(None)
            return
        title = row.folder + ("   · von Programmen verwaltet" if kw.is_managed(row.folder) else "")
        row.set_header(Gtk.Label(label=title, xalign=0, css_classes=["folder-header"]))

    def _on_select(self, _box, row):
        if row is None:
            return
        self.current = (row.folder, row.key)
        self.show_entry(row.folder, row.key)

    # ---------- Detailansicht ----------

    def _clear_detail(self):
        while (child := self.detail.get_first_child()) is not None:
            self.detail.remove(child)
        self.editor = None

    def show_empty(self):
        self._clear_detail()
        self.detail.append(Gtk.Label(label="Links einen Eintrag wählen.", vexpand=True,
                                     css_classes=["dim-label"]))

    def show_entry(self, folder, key):
        self._clear_detail()
        try:
            kind, value = self.wallet.read(folder, key)
        except kw.WalletError as err:
            self.error("Eintrag konnte nicht gelesen werden", err)
            return

        self.detail.append(Gtk.Label(label=key, xalign=0, wrap=True, selectable=True,
                                     css_classes=["entry-title"]))
        self.detail.append(Gtk.Label(label=f"{folder}  ·  {kw.TYPE_NAMES.get(kind, kind)}",
                                     xalign=0, css_classes=["dim-label"]))
        if kw.is_managed(folder):
            self.detail.append(Gtk.Label(
                xalign=0, wrap=True, css_classes=["managed-note"],
                label="Diesen Eintrag verwaltet ein Programm selbst. Wer ihn ändert oder "
                      "löscht, meldet das Programm ab oder macht seine gespeicherten "
                      "Daten unlesbar."))

        if kind == kw.TYPE_PASSWORD:
            self._password_editor(value)
        elif kind == kw.TYPE_MAP:
            self._map_editor(value)
        else:
            self._stream_view(value)

        actions = Gtk.Box(spacing=8, margin_top=12)
        if self.editor:
            save = Gtk.Button(label="Speichern", css_classes=["suggested-action"])
            save.connect("clicked", lambda *_: self.save_current(kind, value))
            actions.append(save)
        rename = Gtk.Button(label="Umbenennen …")
        rename.connect("clicked", lambda *_: self.dialog_rename())
        actions.append(rename)
        delete = Gtk.Button(label="Löschen …", css_classes=["destructive-action"])
        delete.connect("clicked", lambda *_: self.delete_current())
        actions.append(delete)
        self.detail.append(actions)

    def _secret_row(self, text):
        box = Gtk.Box(spacing=6)
        entry = Gtk.PasswordEntry(text=text, show_peek_icon=True, hexpand=True)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Kopieren")
        copy.connect("clicked", lambda *_: self.copy_secret(entry.get_text()))
        box.append(entry)
        box.append(copy)
        return box, entry

    def _password_editor(self, value):
        self.detail.append(Gtk.Label(label="Passwort", xalign=0))
        box, entry = self._secret_row(value)
        self.detail.append(box)
        self.editor = entry.get_text

    def _map_editor(self, mapping):
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        fields = []                    # [(namens-Entry, wert-Entry, zeilen-Widgets)]

        def add_row(name="", text=""):
            row = len(fields)
            name_entry = Gtk.Entry(text=name, width_chars=14, placeholder_text="Feld")
            if is_secret_field(name):
                value_box, value_entry = self._secret_row(text)
            else:
                value_entry = Gtk.Entry(text=text, hexpand=True)
                value_box = value_entry
            remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Feld entfernen")
            grid.attach(name_entry, 0, row, 1, 1)
            grid.attach(value_box, 1, row, 1, 1)
            grid.attach(remove, 2, row, 1, 1)
            item = [name_entry, value_entry, (name_entry, value_box, remove)]
            fields.append(item)

            def drop(*_):
                for widget in item[2]:
                    grid.remove(widget)
                fields.remove(item)
            remove.connect("clicked", drop)

        for name, text in mapping.items():
            add_row(name, text)
        self.detail.append(grid)
        more = Gtk.Button(label="Feld hinzufügen", halign=Gtk.Align.START)
        more.connect("clicked", lambda *_: add_row())
        self.detail.append(more)
        self.editor = lambda: {n.get_text(): v.get_text() for n, v, _ in fields
                               if n.get_text().strip()}

    def _stream_view(self, data):
        self.detail.append(Gtk.Label(
            xalign=0, wrap=True,
            label=f"Binärdaten, {len(data)} Byte. Nur zur Ansicht – bearbeiten lässt "
                  "sich das sinnvoll nur im Programm, das sie angelegt hat."))
        text = " ".join(f"{b:02x}" for b in data[:2048])
        view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.CHAR,
                            css_classes=["mono"], height_request=120)
        view.get_buffer().set_text(text + (" …" if len(data) > 2048 else ""))
        self.detail.append(view)

    # ---------- Aktionen ----------

    def save_current(self, kind, old_value):
        folder, key = self.current
        new_value = self.editor()
        if new_value == old_value:
            self.say("Keine Änderung.")
            return

        def do():
            if self.mutate(f"aendern {key}",
                           lambda: self.wallet.write(folder, key, kind, new_value),
                           f"„{key}“ gespeichert."):
                self.show_entry(folder, key)

        if kw.is_managed(folder):
            self.confirm("Programm-Eintrag wirklich ändern?",
                         f"„{key}“ in „{folder}“ gehört einem Programm.", "Ändern", do)
        else:
            do()

    def delete_current(self):
        folder, key = self.current
        detail = f"„{key}“ aus „{folder}“. Vorher wird automatisch gesichert."
        if kw.is_managed(folder):
            detail += ("\n\nAchtung: Das Programm, dem dieser Eintrag gehört, verliert "
                       "damit seinen Schlüssel.")

        def do():
            if self.mutate(f"loeschen {key}", lambda: self.wallet.remove(folder, key),
                           f"„{key}“ gelöscht."):
                self.current = None
                self.reload()
        self.confirm("Eintrag löschen?", detail, "Löschen", do)

    def _form(self, title, rows, ok_label, on_ok):
        """Kleines modales Formular. rows: [(beschriftung, widget)]."""
        win = Gtk.Window(title=title, transient_for=self, modal=True,
                         default_width=440, resizable=False)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin_top=18,
                        margin_bottom=18, margin_start=18, margin_end=18)
        for i, (label, widget) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, i, 1, 1)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Abbrechen")
        cancel.connect("clicked", lambda *_: win.close())
        ok = Gtk.Button(label=ok_label, css_classes=["suggested-action"])
        ok.connect("clicked", lambda *_: on_ok() and win.close())
        buttons.append(cancel)
        buttons.append(ok)
        grid.attach(buttons, 0, len(rows), 2, 1)
        win.set_child(grid)
        win.present()

    def dialog_rename(self):
        folder, key = self.current
        name = Gtk.Entry(text=key)

        def ok():
            new = name.get_text().strip()
            if not new or new == key:
                return True
            if new in self.wallet.entries(folder):
                self.error("Name schon vergeben", f"„{new}“ gibt es in „{folder}“ bereits.")
                return False
            if self.mutate(f"umbenennen {key}",
                           lambda: self.wallet.rename(folder, key, new),
                           f"„{key}“ heißt jetzt „{new}“."):
                self.reload(select=(folder, new))
            return True
        self._form("Umbenennen", [("Neuer Name", name)], "Umbenennen", ok)

    def dialog_new_entry(self):
        folder = Gtk.Entry(text=self.current[0] if self.current else "Passwords")
        key = Gtk.Entry(placeholder_text="z. B. smb-nas.local:-1")
        kind = Gtk.DropDown.new_from_strings(["Benutzer + Passwort", "Nur Passwort"])
        user = Gtk.Entry()
        secret = Gtk.PasswordEntry(show_peek_icon=True)
        kind.connect("notify::selected",
                     lambda *_: user.set_sensitive(kind.get_selected() == 0))

        def ok():
            f, k = folder.get_text().strip(), key.get_text().strip()
            if not f or not k:
                self.error("Ordner und Name fehlen")
                return False
            if f in self.wallet.folders() and k in self.wallet.entries(f):
                self.error("Eintrag gibt es schon", f"„{k}“ in „{f}“ – bitte dort bearbeiten.")
                return False
            if kind.get_selected() == 0:
                t, v = kw.TYPE_MAP, {"login": user.get_text(), "password": secret.get_text()}
            else:
                t, v = kw.TYPE_PASSWORD, secret.get_text()

            def create():
                if f not in self.wallet.folders():
                    self.wallet.create_folder(f)
                self.wallet.write(f, k, t, v)
            if self.mutate(f"neu {k}", create, f"„{k}“ angelegt."):
                self.reload(select=(f, k))
            return True

        self._form("Neuer Eintrag", [("Ordner", folder), ("Name", key), ("Art", kind),
                                     ("Benutzer", user), ("Passwort", secret)],
                   "Anlegen", ok)

    def backup_now(self):
        try:
            path = backups.save_backup(self.wallet, "manuell")
            self.say(f"Gesichert: {path}")
        except gpgkey.KeyMissing:
            self.setup_key("Danach bitte noch einmal „Jetzt sichern“.")
        except Exception as err:  # noqa: BLE001
            self.error("Sicherung fehlgeschlagen", err)

    # --- Passphrase und Sicherungsschluessel -----------------------------------

    def ask_passphrase(self, title, hint, on_ok, new=False):
        """Eigenes Passphrase-Fenster statt pinentry. new=True: zweimal eingeben."""
        first = Gtk.PasswordEntry(show_peek_icon=True)
        rows = [("Passphrase", first)]
        second = None
        if new:
            second = Gtk.PasswordEntry(show_peek_icon=True)
            rows.append(("Wiederholen", second))
        rows.append(("", Gtk.Label(label=hint, wrap=True, xalign=0, max_width_chars=48)))

        def ok():
            phrase = first.get_text()
            if not phrase:
                return False
            if new and phrase != second.get_text():
                self.error("Passphrasen stimmen nicht überein")
                return False
            if new and len(phrase) < 10:
                self.error("Passphrase zu kurz", "Bitte mindestens 10 Zeichen.")
                return False
            GLib.idle_add(lambda: on_ok(phrase) and False)
            return True
        self._form(title, rows, "OK", ok)

    def setup_key(self, after=""):
        """Einrichtung: neuen Sicherungsschluessel anlegen oder vorhandenen
        importieren. Ohne Schluessel keine Sicherung, ohne Sicherung keine
        Aenderung."""
        if not gpgkey.gpg_installed():
            self.error("gpg fehlt", "Für verschlüsselte Sicherungen muss das Programm "
                       "gpg (GnuPG) installiert sein.")
            return
        dialog = Gtk.AlertDialog(
            message="Sicherungsschlüssel einrichten",
            detail="KWallet-MyManager verschlüsselt jede Sicherung mit einem eigenen "
                   "GPG-Schlüssel. Hier ist noch keiner eingerichtet.\n\n"
                   "Gibt es den Schlüssel schon (Anhang im KeePass-Eintrag "
                   "„KWallet-MyManager Sicherungsschlüssel“), bitte importieren. Dann "
                   "öffnet eine Passphrase die Sicherungen aller Geräte.",
            buttons=["Später", "Neuen Schlüssel anlegen", "Schlüssel importieren …"],
            cancel_button=0, default_button=2)

        def chosen(dlg, result):
            try:
                choice = dlg.choose_finish(result)
            except GLib.Error:
                return
            if choice == 1:
                self._create_key(after)
            elif choice == 2:
                self._import_key(after)
        dialog.choose(self, None, chosen)

    def _import_key(self, after):
        dialog = Gtk.FileDialog(title="Sicherungsschlüssel wählen (.asc)")
        key_filter = Gtk.FileFilter(name="GPG-Schlüssel (*.asc, *.gpg, *.key)")
        for pattern in ("*.asc", "*.gpg", "*.key"):
            key_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(key_filter)
        dialog.set_filters(filters)

        def picked(dlg, result):
            try:
                path = dlg.open_finish(result).get_path()
            except GLib.Error:
                return
            try:
                _, has_secret = gpgkey.import_key(path)
            except Exception as err:  # noqa: BLE001
                self.error("Import fehlgeschlagen", err)
                return
            if not has_secret:
                self.error("Nur der öffentliche Schlüssel importiert",
                           "Sichern geht, Zurückspielen aber erst mit dem privaten "
                           "Schlüssel (Export aus KeePass).")
            self.say("Sicherungsschlüssel eingerichtet. " + after)
        dialog.open(self, None, picked)

    def _create_key(self, after):
        def create(phrase):
            try:
                gpgkey.create_key(phrase)
            except Exception as err:  # noqa: BLE001
                self.error("Schlüssel anlegen fehlgeschlagen", err)
                return
            self.say("Sicherungsschlüssel angelegt. " + after)
            self.export_key(phrase)
        self.ask_passphrase(
            "Neuer Sicherungsschlüssel",
            "Diese Passphrase brauchst du nur zum Zurückspielen. Ohne sie ist keine "
            "Sicherung lesbar: in KeePass ablegen.", create, new=True)

    def export_key(self, phrase=None):
        """Privaten Schluessel als .asc speichern, fuer den KeePass-Anhang."""
        if not gpgkey.ready():
            self.setup_key()
            return
        if phrase is None:
            self.ask_passphrase("Schlüssel exportieren", "Passphrase des "
                                "Sicherungsschlüssels.", self.export_key)
            return
        try:
            blob = gpgkey.export_secret(phrase)
        except Exception as err:  # noqa: BLE001
            self.error("Export fehlgeschlagen", err)
            return
        dialog = Gtk.FileDialog(title="Sicherungsschlüssel speichern",
                                initial_name="kwallet-mymanager-sicherungsschluessel.asc")

        def saved(dlg, result):
            try:
                path = dlg.save_finish(result).get_path()
            except GLib.Error:
                return
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            self.error("Schlüssel gespeichert – jetzt in KeePass ablegen",
                       f"{path}\n\nDie Datei als Anhang an den KeePass-Eintrag "
                       "„KWallet-MyManager Sicherungsschlüssel“ hängen, die Passphrase "
                       "ins Passwortfeld, und die Datei danach löschen.")
        dialog.save(self, None, saved)

    # --- Wiederherstellen ----------------------------------------------------------

    def restore_dialog(self):
        dialog = Gtk.FileDialog(title="Sicherung wählen")
        if backups.BACKUP_DIR.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(backups.BACKUP_DIR)))
        json_filter = Gtk.FileFilter(name="Sicherungen (*.json.gpg, *.json)")
        json_filter.add_pattern("*.json.gpg")
        json_filter.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog.set_filters(filters)

        def picked(dlg, result):
            try:
                path = dlg.open_finish(result).get_path()
            except GLib.Error:
                return
            if backups.needs_passphrase(path):
                self.ask_passphrase("Sicherung entschlüsseln",
                                    "Passphrase des Sicherungsschlüssels.",
                                    lambda phrase: self._restore_from(path, phrase))
            else:
                self._restore_from(path, None)
        dialog.open(self, None, picked)

    def _restore_from(self, path, phrase):
        try:
            folders, meta = backups.load_backup(path, phrase)
        except gpgkey.BadPassphrase:
            self.error("Passphrase falsch", "Bitte noch einmal versuchen.")
            return
        except Exception as err:  # noqa: BLE001
            self.error("Sicherung nicht lesbar", err)
            return
        count = sum(len(e) for e in folders.values())

        def do():
            try:
                written = backups.restore(self.wallet, folders)
            except Exception as err:  # noqa: BLE001
                self.error("Wiederherstellen fehlgeschlagen", err)
                return
            self.say(f"Wiederhergestellt: {written} Einträge geschrieben.")
            self.reload()
        self.confirm(
            "Sicherung zurückspielen?",
            f"Stand {meta.get('created')}, Anlass „{meta.get('reason')}“, "
            f"{count} Einträge.\n\nFehlende und abweichende Einträge werden "
            "zurückgeschrieben, neuere zusätzliche bleiben stehen. Vorher wird "
            "der jetzige Stand gesichert.", "Zurückspielen", do)


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window = None
        self.wallet = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, handler in (("backup", lambda *_: self.window.backup_now()),
                              ("restore", lambda *_: self.window.restore_dialog()),
                              ("open-backups", lambda *_: self.open_backup_dir()),
                              ("export-key", lambda *_: self.window.export_key())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def open_backup_dir(self):
        backups.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(backups.BACKUP_DIR.as_uri(), None)

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        try:
            self.wallet = kw.Wallet()
            self.wallet.ensure_open()
        except kw.WalletError as err:
            print(f"KWallet nicht erreichbar: {err}", file=sys.stderr)
            self.quit()
            return
        self.window = MainWindow(self, self.wallet)
        self.window.present()
        if not gpgkey.ready():
            self.window.setup_key()

    def do_shutdown(self):
        if self.wallet:
            self.wallet.close()
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))

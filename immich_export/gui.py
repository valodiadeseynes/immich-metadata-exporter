"""Interface GTK 3 d'Immich Export."""

from __future__ import annotations

import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_ID, APP_NAME, __version__, config  # noqa: E402
from .api import ImmichClient, ImmichError  # noqa: E402
from .exporter import MODE_BOTH, MODE_EMBED, MODE_SIDECAR, ExportSettings, export_asset, exiftool_path  # noqa: E402
from .metadata import Options  # noqa: E402

THUMB = 150
COL_PIXBUF, COL_ID, COL_TOOLTIP = 0, 1, 2


def run_bg(func, on_done=None, on_error=None):
    """Exécute func() dans un thread ; rappels exécutés dans la boucle GTK."""

    def worker():
        try:
            res = func()
        except Exception as e:  # noqa: BLE001
            if on_error:
                GLib.idle_add(on_error, e)
            return
        if on_done:
            GLib.idle_add(on_done, res)

    threading.Thread(target=worker, daemon=True).start()


def placeholder() -> GdkPixbuf.Pixbuf:
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, THUMB, THUMB)
    pb.fill(0x3C3C3CFF)
    return pb


def decode_thumb(data: bytes) -> GdkPixbuf.Pixbuf | None:
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
    except GLib.Error:
        return None
    if pb is None:
        return None
    w, h = pb.get_width(), pb.get_height()
    scale = THUMB / max(w, h)
    return pb.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR)


def iso_day(text: str, end: bool) -> str | None:
    text = text.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"Date invalide : « {text} » (format attendu AAAA-MM-JJ)")
    return text + ("T23:59:59.999Z" if end else "T00:00:00.000Z")


# =========================================================================== réglages
class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, cfg):
        super().__init__(title="Connexion au serveur Immich", transient_for=parent, modal=True)
        self.add_buttons("Annuler", Gtk.ResponseType.CANCEL, "Enregistrer", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_default_size(480, -1)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=16)
        self.url = Gtk.Entry(text=cfg["url"], hexpand=True, placeholder_text="https://photos.exemple.fr")
        self.key = Gtk.Entry(text=config.get_api_key(cfg["url"]) if cfg["url"] else "", visibility=False)
        self.key.set_activates_default(True)
        self.tls = Gtk.CheckButton(label="Vérifier le certificat TLS", active=cfg["verify_tls"])
        test = Gtk.Button(label="Tester la connexion")
        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        test.connect("clicked", self.on_test)

        help_ = Gtk.Label(xalign=0, wrap=True)
        help_.set_markup(
            "<small>Créez une clé dans Immich : <i>Paramètres du compte → Clés API</i>.\n"
            "Permissions nécessaires : asset.read, asset.view, asset.download, album.read, "
            "person.read, face.read.</small>"
        )
        grid.attach(Gtk.Label(label="URL du serveur", xalign=0), 0, 0, 1, 1)
        grid.attach(self.url, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Clé API", xalign=0), 0, 1, 1, 1)
        grid.attach(self.key, 1, 1, 1, 1)
        grid.attach(self.tls, 1, 2, 1, 1)
        grid.attach(help_, 0, 3, 2, 1)
        grid.attach(test, 0, 4, 1, 1)
        grid.attach(self.status, 1, 4, 1, 1)
        self.get_content_area().add(grid)
        self.show_all()

    def values(self):
        return self.url.get_text().strip(), self.key.get_text().strip(), self.tls.get_active()

    def on_test(self, _btn):
        url, key, tls = self.values()
        self.status.set_text("Test en cours…")

        def check():
            c = ImmichClient(url, key, tls)
            v = c.server_version()
            n = len(c.albums())
            return f"✔ Immich {v} — {n} album(s) accessibles"

        run_bg(check, self.status.set_text, lambda e: self.status.set_text(f"✘ {e}"))


# =========================================================================== fenêtre
class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1150, 750)
        self.cfg = config.load()
        self.client: ImmichClient | None = None
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.generation = 0
        self.fetch_page = None
        self.next_page = None
        self.busy = 0
        self.cancel_event: threading.Event | None = None
        self.placeholder = placeholder()

        self._build_header()
        self._build_body()
        self.connect("destroy", lambda *_: self.pool.shutdown(wait=False, cancel_futures=True))
        self.show_all()
        self.more_btn.hide()
        self.cancel_btn.hide()

        GLib.idle_add(self._startup_checks)

    # ------------------------------------------------------------------ construction
    def _build_header(self):
        hb = Gtk.HeaderBar(show_close_button=True, title=APP_NAME, subtitle="Non connecté")
        self.header = hb
        settings = Gtk.Button.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.BUTTON)
        settings.set_tooltip_text("Connexion au serveur")
        settings.connect("clicked", lambda *_: self.open_settings())
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Recharger albums et personnes")
        refresh.connect("clicked", lambda *_: self.load_sidebar())
        self.spinner = Gtk.Spinner()
        hb.pack_end(settings)
        hb.pack_end(refresh)
        hb.pack_end(self.spinner)
        self.set_titlebar(hb)

    def _build_body(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=280)
        self.add(paned)

        # --- panneau gauche
        nb = Gtk.Notebook()
        paned.pack1(nb, resize=False, shrink=False)

        self.album_list = Gtk.ListBox()
        self.album_list.connect("row-activated", self.on_album)
        self.album_filter = Gtk.SearchEntry(placeholder_text="Filtrer les albums")
        nb.append_page(self._filtered_list(self.album_list, self.album_filter), Gtk.Label(label="Albums"))

        self.people_list = Gtk.ListBox()
        self.people_list.connect("row-activated", self.on_person)
        self.people_filter = Gtk.SearchEntry(placeholder_text="Filtrer les personnes")
        nb.append_page(self._filtered_list(self.people_list, self.people_filter), Gtk.Label(label="Personnes"))

        nb.append_page(self._search_page(), Gtk.Label(label="Recherche"))

        # --- panneau droit
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        paned.pack2(right, resize=True, shrink=False)

        self.source_label = Gtk.Label(xalign=0, margin=8)
        self.source_label.set_markup("<b>Choisissez un album, une personne ou lancez une recherche.</b>")
        right.pack_start(self.source_label, False, False, 0)

        self.store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)
        self.icons = Gtk.IconView(model=self.store)
        self.icons.set_pixbuf_column(COL_PIXBUF)
        self.icons.set_tooltip_column(COL_TOOLTIP)
        self.icons.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.icons.set_item_width(THUMB)
        self.icons.set_item_padding(2)
        self.icons.set_margin(6)
        self.icons.connect("selection-changed", self.on_selection)
        self.icons.connect("item-activated", self.on_item_activated)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(self.icons)
        right.pack_start(sw, True, True, 0)

        self.more_btn = Gtk.Button(label="Charger la suite…", margin=4)
        self.more_btn.connect("clicked", lambda *_: self.load_page(append=True))
        right.pack_start(self.more_btn, False, False, 0)

        right.pack_start(Gtk.Separator(), False, False, 0)
        right.pack_start(self._action_bar(), False, False, 0)

    def _filtered_list(self, listbox, entry):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=4)
        box.pack_start(entry, False, False, 0)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.add(listbox)
        box.pack_start(sw, True, True, 0)

        def filt(row):
            t = entry.get_text().strip().lower()
            return not t or t in row.search_text

        listbox.set_filter_func(filt)
        entry.connect("search-changed", lambda *_: listbox.invalidate_filter())
        return box

    def _search_page(self):
        g = Gtk.Grid(column_spacing=8, row_spacing=8, margin=10)
        self.q_smart = Gtk.Entry(placeholder_text="ex. : plage au coucher du soleil", hexpand=True)
        self.q_name = Gtk.Entry(placeholder_text="ex. : IMG_2024")
        self.q_from = Gtk.Entry(placeholder_text="AAAA-MM-JJ")
        self.q_to = Gtk.Entry(placeholder_text="AAAA-MM-JJ")
        self.q_fav = Gtk.CheckButton(label="Favoris uniquement")
        btn = Gtk.Button(label="Rechercher")
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", self.on_search)
        for e in (self.q_smart, self.q_name, self.q_from, self.q_to):
            e.connect("activate", self.on_search)
        rows = [
            ("Recherche intelligente", self.q_smart),
            ("Nom de fichier", self.q_name),
            ("Prise de vue du", self.q_from),
            ("au", self.q_to),
        ]
        for i, (lbl, w) in enumerate(rows):
            g.attach(Gtk.Label(label=lbl, xalign=0), 0, i, 1, 1)
            g.attach(w, 1, i, 1, 1)
        g.attach(self.q_fav, 1, len(rows), 1, 1)
        g.attach(btn, 1, len(rows) + 1, 1, 1)
        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=30)
        note.set_markup(
            "<small>La recherche intelligente (CLIP) décrit le contenu de l'image. "
            "Sans texte, la recherche se fait sur les métadonnées.</small>"
        )
        g.attach(note, 0, len(rows) + 2, 2, 1)
        return g

    def _action_bar(self):
        bar = Gtk.Box(spacing=6, margin=8)
        sel_all = Gtk.Button(label="Tout sélectionner")
        sel_all.connect("clicked", lambda *_: self.icons.select_all())
        sel_none = Gtk.Button(label="Aucun")
        sel_none.connect("clicked", lambda *_: self.icons.unselect_all())
        self.sel_label = Gtk.Label(label="0 sélectionné")
        bar.pack_start(sel_all, False, False, 0)
        bar.pack_start(sel_none, False, False, 0)
        bar.pack_start(self.sel_label, False, False, 6)

        self.progress = Gtk.ProgressBar(show_text=True, valign=Gtk.Align.CENTER, hexpand=True)
        bar.pack_start(self.progress, True, True, 6)

        self.dest = Gtk.FileChooserButton(title="Dossier de destination", action=Gtk.FileChooserAction.SELECT_FOLDER)
        dest = self.cfg["dest_dir"]
        if not os.path.isdir(dest):
            dest = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~")
        self.dest.set_filename(dest)
        self.dest.set_tooltip_text("Dossier de destination")
        self.dest.set_width_chars(18)
        bar.pack_start(self.dest, False, False, 0)

        opts = Gtk.MenuButton(label="Options")
        opts.set_popover(self._options_popover())
        bar.pack_start(opts, False, False, 0)

        self.cancel_btn = Gtk.Button(label="Annuler")
        self.cancel_btn.connect("clicked", lambda *_: self.cancel_event and self.cancel_event.set())
        bar.pack_start(self.cancel_btn, False, False, 0)

        self.dl_btn = Gtk.Button(label="Télécharger")
        self.dl_btn.get_style_context().add_class("suggested-action")
        self.dl_btn.set_sensitive(False)
        self.dl_btn.connect("clicked", self.on_download)
        bar.pack_start(self.dl_btn, False, False, 0)
        return bar

    def _options_popover(self):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        box.pack_start(Gtk.Label(label="Où écrire les métadonnées", xalign=0), False, False, 0)
        r1 = Gtk.RadioButton.new_with_label_from_widget(None, "Dans le fichier (recommandé)")
        r2 = Gtk.RadioButton.new_with_label_from_widget(r1, "Fichier .xmp à côté (original intact)")
        r3 = Gtk.RadioButton.new_with_label_from_widget(r1, "Les deux")
        self.mode_buttons = {MODE_EMBED: r1, MODE_SIDECAR: r2, MODE_BOTH: r3}
        self.mode_buttons.get(self.cfg["mode"], r1).set_active(True)
        for r in (r1, r2, r3):
            box.pack_start(r, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 4)
        self.chk = {}
        for key, label in (
            ("write_faces", "Zones de visage nommées (norme MWG)"),
            ("people_as_keywords", "Ajouter les personnes aux mots-clés"),
            ("albums_as_keywords", "Ajouter les albums aux mots-clés"),
            ("set_mtime", "Date du fichier = date de prise de vue"),
            ("skip_existing", "Ignorer les fichiers déjà présents"),
        ):
            c = Gtk.CheckButton(label=label, active=self.cfg[key])
            self.chk[key] = c
            box.pack_start(c, False, False, 0)
        box.show_all()
        pop.add(box)
        return pop

    # ------------------------------------------------------------------ utilitaires
    def set_busy(self, on: bool):
        self.busy = max(0, self.busy + (1 if on else -1))
        (self.spinner.start if self.busy else self.spinner.stop)()

    def error(self, text, detail=""):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.CLOSE, text=text)
        if detail:
            d.format_secondary_text(str(detail))
        d.run()
        d.destroy()

    def _startup_checks(self):
        if not exiftool_path():
            self.error("exiftool est introuvable.",
                       "Installez-le avec : sudo apt install libimage-exiftool-perl")
        formats = {f.get_name() for f in GdkPixbuf.Pixbuf.get_formats()}
        if "webp" not in formats:
            self.source_label.set_markup(
                "<b>Vignettes WebP non supportées :</b> installez le paquet <tt>webp-pixbuf-loader</tt> "
                "puis relancez l'application."
            )
        if self.cfg["url"] and config.get_api_key(self.cfg["url"]):
            self.connect_server()
        else:
            self.open_settings()
        return False

    # ------------------------------------------------------------------ connexion
    def open_settings(self):
        d = SettingsDialog(self, self.cfg)
        if d.run() == Gtk.ResponseType.OK:
            url, key, tls = d.values()
            self.cfg.update(url=url, verify_tls=tls)
            config.save(self.cfg)
            where = config.set_api_key(url, key)
            if where == "fichier":
                print("Trousseau indisponible : clé API stockée dans", config.KEY_FILE, file=sys.stderr)
            d.destroy()
            self.connect_server()
        else:
            d.destroy()

    def connect_server(self):
        key = config.get_api_key(self.cfg["url"])
        self.client = ImmichClient(self.cfg["url"], key, self.cfg["verify_tls"])
        self.set_busy(True)

        def done(v):
            self.set_busy(False)
            self.header.set_subtitle(f"{self.cfg['url']} — Immich {v}")
            self.load_sidebar()

        def fail(e):
            self.set_busy(False)
            self.header.set_subtitle("Non connecté")
            self.error("Connexion au serveur impossible.", e)

        run_bg(self.client.server_version, done, fail)

    def load_sidebar(self):
        if not self.client:
            return
        self.set_busy(True)
        client = self.client

        def done(res):
            albums, people = res
            self.set_busy(False)
            self._fill(self.album_list, [
                (a["albumName"] or "(sans nom)", f"{a.get('assetCount', '?')}", {"albumIds": [a["id"]]})
                for a in albums
            ])
            self._fill(self.people_list, [(p["name"], "", {"personIds": [p["id"]]}) for p in people])

        def fail(e):
            self.set_busy(False)
            self.error("Chargement des albums ou des personnes impossible.", e)

        run_bg(lambda: (client.albums(), client.people()), done, fail)

    def _fill(self, listbox, items):
        for child in listbox.get_children():
            listbox.remove(child)
        for name, extra, flt in items:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=6, margin=6)
            lbl = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            box.pack_start(lbl, True, True, 0)
            if extra:
                cnt = Gtk.Label(label=extra)
                cnt.get_style_context().add_class("dim-label")
                box.pack_end(cnt, False, False, 0)
            row.add(box)
            row.filters = flt
            row.title = name
            row.search_text = name.lower()
            listbox.add(row)
        listbox.show_all()

    # ------------------------------------------------------------------ sources
    def on_album(self, _lb, row):
        self.start_query(f"Album : {row.title}", lambda p: self.client.search_metadata(page=p, **row.filters))

    def on_person(self, _lb, row):
        self.start_query(f"Personne : {row.title}", lambda p: self.client.search_metadata(page=p, **row.filters))

    def on_search(self, *_):
        if not self.client:
            return
        try:
            after = iso_day(self.q_from.get_text(), False)
            before = iso_day(self.q_to.get_text(), True)
        except ValueError as e:
            self.error(str(e))
            return
        smart = self.q_smart.get_text().strip()
        name = self.q_name.get_text().strip()
        fav = True if self.q_fav.get_active() else None
        filters = {"takenAfter": after, "takenBefore": before, "isFavorite": fav}
        if smart:
            # la recherche intelligente n'accepte pas de filtre sur le nom de fichier
            self.start_query(f"Recherche : « {smart} »",
                             lambda p: self.client.search_smart(smart, page=p, **filters))
        else:
            filters["originalFileName"] = name or None
            self.start_query("Recherche par métadonnées",
                             lambda p: self.client.search_metadata(page=p, **filters))

    def start_query(self, title, fetch_page):
        if not self.client:
            return
        self.generation += 1
        self.store.clear()
        self.source_title = title
        self.fetch_page = fetch_page
        self.next_page = 1
        self.load_page(append=False)

    def load_page(self, append=True):
        if not self.fetch_page or not self.next_page:
            return
        gen, page, fetch = self.generation, self.next_page, self.fetch_page
        self.more_btn.set_sensitive(False)
        self.set_busy(True)

        def done(res):
            self.set_busy(False)
            self.more_btn.set_sensitive(True)
            if gen != self.generation:
                return
            items, nxt = res
            self.next_page = nxt
            self.more_btn.set_visible(bool(nxt))
            for a in items:
                self._add_asset(a, gen)
            n = len(self.store)
            self.source_label.set_markup(
                f"<b>{GLib.markup_escape_text(self.source_title)}</b> — {n} élément(s)"
                + (" (d'autres sont disponibles)" if nxt else "")
            )

        def fail(e):
            self.set_busy(False)
            self.more_btn.set_sensitive(True)
            self.error("La recherche a échoué.", e)

        run_bg(lambda: fetch(page), done, fail)

    def _add_asset(self, a, gen):
        date = (a.get("localDateTime") or "")[:16].replace("T", " ")
        kind = "🎬 " if a.get("type") == "VIDEO" else ""
        tip = GLib.markup_escape_text(f"{kind}{a.get('originalFileName', '')}\n{date}")
        it = self.store.append([self.placeholder, a["id"], tip])
        ref = Gtk.TreeRowReference.new(self.store, self.store.get_path(it))
        client = self.client

        def fetch():
            if gen != self.generation:
                return None
            return decode_thumb(client.thumbnail(a["id"]))

        def apply(fut):
            try:
                pb = fut.result()
            except Exception:  # noqa: BLE001
                return
            if pb is not None:
                GLib.idle_add(self._set_thumb, ref, pb, gen)

        self.pool.submit(fetch).add_done_callback(apply)

    def _set_thumb(self, ref, pb, gen):
        if gen == self.generation and ref.valid():
            self.store[ref.get_path()][COL_PIXBUF] = pb
        return False

    def on_selection(self, *_):
        n = len(self.icons.get_selected_items())
        self.sel_label.set_text(f"{n} sélectionné{'s' if n > 1 else ''}")
        self.dl_btn.set_sensitive(n > 0 and self.cancel_event is None)

    def on_item_activated(self, _iv, path):
        asset_id = self.store[path][COL_ID]
        web = self.client.base[: -len("/api")]
        Gtk.show_uri_on_window(self, f"{web}/photos/{asset_id}", Gtk.get_current_event_time())

    # ------------------------------------------------------------------ export
    def _collect_settings(self) -> ExportSettings:
        mode = next(m for m, b in self.mode_buttons.items() if b.get_active())
        dest = self.dest.get_filename() or self.cfg["dest_dir"]
        for k, c in self.chk.items():
            self.cfg[k] = c.get_active()
        self.cfg.update(mode=mode, dest_dir=dest)
        config.save(self.cfg)
        return ExportSettings(
            dest_dir=dest,
            mode=mode,
            opts=Options(
                people_as_keywords=self.cfg["people_as_keywords"],
                albums_as_keywords=self.cfg["albums_as_keywords"],
                write_faces=self.cfg["write_faces"],
            ),
            set_mtime=self.cfg["set_mtime"],
            skip_existing=self.cfg["skip_existing"],
        )

    def on_download(self, *_):
        if not exiftool_path():
            self.error("exiftool est introuvable.", "sudo apt install libimage-exiftool-perl")
            return
        paths = sorted(self.icons.get_selected_items(), key=lambda p: p.get_indices()[0])
        ids = [self.store[p][COL_ID] for p in paths]
        settings = self._collect_settings()
        client = self.client
        cancel = self.cancel_event = threading.Event()
        self.dl_btn.set_sensitive(False)
        self.cancel_btn.show()
        total = len(ids)

        def progress(i, name):
            self.progress.set_fraction(i / total)
            self.progress.set_text(f"{i}/{total} {name}")
            return False

        def work():
            results = []
            for i, aid in enumerate(ids):
                if cancel.is_set():
                    break
                GLib.idle_add(progress, i, "")
                r = export_asset(client, aid, settings, cancel)
                results.append(r)
                GLib.idle_add(progress, i + 1, os.path.basename(r.path or ""))
            return results

        run_bg(work, lambda res: self._export_done(res, settings, total),
               lambda e: self._export_done([], settings, total, e))

    def _export_done(self, results, settings, total, exc=None):
        self.cancel_event = None
        self.cancel_btn.hide()
        self.on_selection()
        ok = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok]
        warn = [r for r in ok if r.warning]
        self.progress.set_fraction(1.0 if results else 0.0)
        self.progress.set_text(f"{len(ok)}/{total} téléchargé(s)")

        lines = [f"✘ {os.path.basename(r.path or r.asset_id)} : {r.error}" for r in bad]
        lines += [f"⚠ {os.path.basename(r.path or '')} : {r.warning}" for r in warn]
        if exc:
            lines.insert(0, f"Erreur : {exc}")

        d = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING if (bad or exc) else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text=f"{len(ok)} fichier(s) sur {total} enregistré(s) dans {settings.dest_dir}",
        )
        d.add_buttons("Ouvrir le dossier", 1, "Fermer", Gtk.ResponseType.CLOSE)
        if lines:
            tv = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
            tv.get_buffer().set_text("\n".join(lines))
            sw = Gtk.ScrolledWindow(min_content_height=160, min_content_width=520)
            sw.add(tv)
            d.get_message_area().pack_start(sw, True, True, 0)
            sw.show_all()
        if d.run() == 1:
            Gtk.show_uri_on_window(self, Gio.File.new_for_path(settings.dest_dir).get_uri(), Gtk.get_current_event_time())
        d.destroy()


# =========================================================================== application
class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.get_active_window() or MainWindow(self)
        win.present()


def main():
    GLib.set_application_name(APP_NAME)
    print(f"{APP_NAME} {__version__}", file=sys.stderr)
    return App().run(sys.argv)

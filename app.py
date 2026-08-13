"""
Octo Updater GUI — the Tk presentation adapter (SlimScrollbar + OctoUpdaterApp).

Launch via octo_updater.py (the entry point). This module wires the config
store paths at import time and renders the state the Phase 1b controllers
own: it builds widgets, binds events, renders controller state and forwards
user actions back into the controllers. No business logic lives here.
"""

import os
import queue
import tkinter as tk
from tkinter import filedialog

from helpers import (
    parse_wow_colored,
    strip_wow_colors,
    strip_html as _strip_html,
    format_news_date as _format_news_date,
)

import config_store
# Only the tweak apply/reset workers still touch the config directly — the
# tweaks controller is deliberately deferred to a later phase.
from config_store import (
    load_config,
    update_config,
)

from log_sink import log, _LOG_Q

from filesystem import sha1_file

from tweaks import (
    TWEAKS_DEFAULTS,
    TWEAKS_ITEMS,
    TWEAKS_LIMITS,
    fov_default_for_display,
    load_tweaks_config,
    run_apply_worker_in_background,
    save_tweaks_config,
    update_config_wtf,
)

from client_update import UpdateWorker

from ui_events import (
    AddonsLoaded,
    EventDispatcher,
    LogMessage,
    MirrorStatusChanged,
    ModsLoaded,
    NewsLoaded,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)

import addons_controller
import mods_controller
import news_controller
import settings_controller
import update_controller

# ──────────────────────────────────────────────────────────────────────────────
#  Constants  (see constants.py)
# ──────────────────────────────────────────────────────────────────────────────

from constants import (
    UPDATER_VERSION,
    CONFIG_FILE,
    CACHE_FILE,
    DEFAULT_OUT_DIR,
    LEGACY_CONFIG_FILE,
    LEGACY_CACHE_FILE,
)

import platform_support
from platform_support import (
    is_windows,
    can_launch_client,
    can_patch_client,
    can_manage_antivirus,
    ui_font_family,
)

import ui_metrics
from ui_metrics import (
    UIScale,
    FOOT_H,
    HDR_H,
    PANEL_PAD,
    initial_window_size,
    layout_mode,
    news_columns,
    panel_rect,
    progress_width,
    settings_rect,
)

config_store.configure(CONFIG_FILE, CACHE_FILE,
                       LEGACY_CONFIG_FILE, LEGACY_CACHE_FILE)

# Logical design size; the actual window scales from here (see UIScale).
WIN_W, WIN_H = 1000, 700

C_BG         = "#120e1a"
C_PANEL      = "#161120"
C_PANEL_BDR  = "#261d3a"
C_HDR        = "#0d0a14"
C_DIVIDER    = "#2a2142"
C_GOLD       = "#c8922a"
C_GOLD_LT    = "#e8b84b"
C_PURPLE     = "#8a4fa5"
C_GREEN_BTN  = "#4a7c2f"
C_GREEN_HOV  = "#5a9438"
C_TEXT       = "#d8d4cc"
C_TEXT_DIM   = "#7a7670"
C_LOG_BG     = "#0f0b16"
C_OK         = "#6abf69"
C_ERR        = "#bf6969"
C_MOD_HL     = "#a8b83c"   # olive-green highlight for installed mods

# Parchment palette for the featured news post
C_PARCH       = "#e9dcb8"
C_PARCH_BAND  = "#ddcda0"
C_PARCH_LINE  = "#c3b083"
C_PARCH_TITLE = "#7c5a12"
C_PARCH_TEXT  = "#3a352a"
C_PARCH_DIM   = "#8b8064"
C_PARCH_LINK  = "#a3561c"
C_PARCH_EDGE  = "#b7a678"

_UI_FONT = ui_font_family()


# ──────────────────────────────────────────────────────────────────────────────
#  GUI
# ──────────────────────────────────────────────────────────────────────────────


class SlimScrollbar(tk.Canvas):
    """Flat minimal scrollbar (the native tk.Scrollbar can't be themed on
    Windows). Speaks the standard set()/command scrollbar protocol."""

    def __init__(self, parent, command=None, width=10,
                 bg=C_PANEL, thumb="#3a2f55", thumb_hover=C_GOLD, **kw):
        super().__init__(parent, width=width, bg=bg,
                         highlightthickness=0, bd=0, **kw)
        self.command      = command
        self._thumb       = thumb
        self._thumb_hover = thumb_hover
        self._first       = 0.0
        self._last        = 1.0
        self._drag_off    = None
        self._hover       = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>",  self._click)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Enter>",     lambda e: self._set_hover(True))
        self.bind("<Leave>",     lambda e: self._set_hover(False))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._redraw()

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _redraw(self):
        self.delete("all")
        if self._last - self._first >= 1.0:
            return
        h  = self.winfo_height()
        w  = self.winfo_width()
        y0 = int(self._first * h)
        y1 = max(int(self._last * h), y0 + 24)
        self.create_rectangle(
            2, y0, w - 2, y1,
            fill=self._thumb_hover if self._hover else self._thumb,
            outline="")

    def _click(self, e):
        h  = self.winfo_height() or 1
        y0 = self._first * h
        y1 = self._last * h
        self._drag_off = (e.y - y0) if y0 <= e.y <= y1 else (y1 - y0) / 2
        self._drag(e)

    def _drag(self, e):
        h     = self.winfo_height() or 1
        span  = self._last - self._first
        first = max(0.0, min((e.y - (self._drag_off or 0)) / h, 1.0 - span))
        if self.command:
            self.command("moveto", first)

class OctoUpdaterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        # Keep the window hidden until it's positioned and fully built, so it
        # never flashes at the default top-left corner before centering.
        self.withdraw()

        # First-run flags live in the SettingsController's SettingsState;
        # they were detected there before anything writes the config.
        # Session log lives in memory; the "Show logs" window renders it.
        self._log_buffer: list = []
        self._logwin = None
        self._logwin_text = None
        self._settings_overlay = None

        # Phase 1b controllers own the update/verify/mod/news/addons/settings
        # business logic; this class renders their events. All UI updates
        # arrive via the dispatcher on the main thread.
        self._events = EventDispatcher()
        self._updater = update_controller.UpdateController(
            self._events, get_out_dir=lambda: self._path_var.get().strip())
        self._news = news_controller.NewsController(self._events)
        self._mods = mods_controller.ModsController(
            self._events, get_out_dir=lambda: self._path_var.get().strip())
        self._addons = addons_controller.AddonsController(
            self._events, get_out_dir=lambda: self._path_var.get().strip())

        # Settings/game-folder controller — owns the loaded config and
        # SettingsState.path, which is mirrored against _path_var below (and
        # kept in sync by the trace).
        self._settings = settings_controller.SettingsController(
            self._events, self._updater, self._mods, self._addons, self._news)
        # _cfg mirrors the settings controller's live config snapshot; every
        # mutation goes through it so the Tk side never touches the store.
        self._cfg = self._settings.state.config
        self._path_var = tk.StringVar(
            value=os.path.normpath(self._cfg.get("out_dir", DEFAULT_OUT_DIR)))
        self._last_path_val = os.path.normpath(self._path_var.get().strip())
        self._settings.state.path = os.path.normpath(
            self._path_var.get().strip())
        self._path_var.trace_add("write", self._on_path_changed)

        self._events.subscribe(self._on_status_changed)
        self._events.subscribe(self._on_progress_changed)
        self._events.subscribe(self._on_log_message)
        self._events.subscribe(self._on_operation_finished)
        self._events.subscribe(self._on_operation_failed)
        self._events.subscribe(self._on_news_loaded)
        self._events.subscribe(self._on_mods_loaded)
        self._events.subscribe(self._on_addons_loaded)
        self._events.subscribe(self._on_mirror_status_changed)

        # Count of mods with an update available — shown as a badge on the
        # MODS nav tab. Mirror of the ModsController's updates_count.
        self._mod_updates_count = self._mods.updates_count

        # Mirror of the AddonsController's updates_count — shown as a badge
        # on the ADDONS nav tab; only redrawn when it actually changes.
        self._addon_updates_count = self._addons.updates_count
        # Last-rendered addon content (addons dict, available list) — lets
        # the AddonsLoaded handler skip a full rebuild when nothing changed.
        self._addons_rendered = None

        # Scrollable list canvases that respond to the mouse wheel whenever
        # the pointer is anywhere over them (not just over the scrollbar).
        self._wheel_canvases: list = []
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        # Linux/X11 reports wheel clicks as Button-4/Button-5 events.
        self.bind_all("<Button-4>", self._on_wheel_button)
        self.bind_all("<Button-5>", self._on_wheel_button)

        self.title("Octo Updater")
        self.configure(bg=C_BG)

        # DPI-aware scaling: detect the display scale factor and size the
        # window for it. Fonts are emitted by _ui.font()/_ui.mono() as
        # pixel-based specs, so they scale even where Tk ignores `tk scaling`.
        self._ui = UIScale(self)
        self._font = self._ui.font
        self._mono = self._ui.mono
        try:
            self.tk.call("tk", "scaling", self._ui.tk_scaling())
        except tk.TclError:
            pass

        self.resizable(True, True)
        self.minsize(ui_metrics.clamp(WIN_W // 2, 560, 800),
                     ui_metrics.clamp(WIN_H // 2, 420, 600))

        # Center on screen and size to ~90% of the available space.
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self._win_w, self._win_h = initial_window_size(self._ui, sw, sh)
        x = (sw - self._win_w) // 2
        y = (sh - self._win_h) // 2
        self.geometry(f"{self._win_w}x{self._win_h}+{x}+{y}")

        # Debounced relayout on resize — recomputes panel geometry.
        self._resize_job = None
        self.bind("<Configure>", self._on_resize)

        self._build()

        out_dir = self._cfg.get("out_dir", DEFAULT_OUT_DIR)
        if not os.path.exists(out_dir):
            self._cfg = self._settings.prune_folder_records()

        live_ver = self._updater.read_client_version()
        if live_ver:
            self._client_ver_var.set(live_ver)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()
        # On first run, defer verification until Settings is closed (see
        # _close_settings / first_run_verify_pending).
        if not self._settings.state.first_run:
            self.after(300, self._start_verify)
        self.after(600, self._load_news)
        # Check mod updates at launch too — but only once mods have actually
        # been used (mod_release_cache exists). On a first run or right after a
        # game-folder change nothing is installed yet, so there's nothing to
        # check; the MODS tab will do it when opened.
        if self._cfg.get("mod_release_cache"):
            self.after(900, self._mods.load_latest_versions)
        # Same parity for addons: background verify at launch (feeds the
        # ADDONS tab badge), but only once addons were initialized for this
        # folder — never on a first run / fresh folder.
        if self._cfg.get("addons") is not None:
            self.after(1500, self._addons_verify)
        # Daily self-update check (cached), last so it never delays the rest.
        self.after(2000, self._updater.check_updater_update)
        # First launch: open Settings so the user sets the game folder etc.
        if self._settings.state.first_run:
            self.after(500, self._open_settings)

        # Everything is positioned and built — reveal the centered window.
        self.deiconify()

    # ── build ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        """Hide the window first so the close feels instant
        Config/caches are already saved at write time,
        and the worker threads are daemons, so nothing blocks the exit."""
        try:
            self.withdraw()
        except Exception:
            pass
        self.quit()

    def _add_tooltip(self, widget, text: str):
        """Attach a small hover tooltip to a widget."""
        state = {"win": None}

        def show(_e=None):
            if state["win"] is not None:
                return
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, font=self._font(9),
                     fg=C_TEXT, bg="#0f0b16",
                     highlightthickness=1, highlightbackground=C_PANEL_BDR,
                     padx=6, pady=2).pack()
            state["win"] = tw

        def hide(_e=None):
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _on_mousewheel(self, event):
        # Windows/macOS report a smooth delta (multiples of 120).
        self._scroll_wheel(-event.delta / 120, event)

    def _on_wheel_button(self, event):
        # Linux/X11: Button-4 scrolls up (+1), Button-5 scrolls down (-1).
        self._scroll_wheel(1 if event.num == 4 else -1, event)

    def _scroll_wheel(self, units: float, event):
        # Panels are stacked, so several list canvases share the same screen
        # region — only the one inside the active tab's panel should scroll.
        active = getattr(self, "_active_panel", None)
        active_prefix = (str(active) + ".") if active is not None else None
        for cv in list(self._wheel_canvases):
            try:
                if not cv.winfo_ismapped():
                    continue
                if active_prefix and not str(cv).startswith(active_prefix):
                    continue
                wx, wy = cv.winfo_rootx(), cv.winfo_rooty()
                if (wx <= event.x_root <= wx + cv.winfo_width()
                        and wy <= event.y_root <= wy + cv.winfo_height()):
                    # When the content fits entirely in view, Tk would still
                    # happily shift it around — ignore the wheel instead.
                    first, last = cv.yview()
                    if last - first < 1.0:
                        cv.yview_scroll(int(units), "units")
                    return
            except tk.TclError:
                self._wheel_canvases.remove(cv)

    def _draw_nav_tab(self, tab: str, hover: bool = False):
        """Render a nav tab on the header canvas; the active tab's text gets
        a soft glow (dim gold halo layers behind the bright text)."""
        cv = self._hdr_canvas
        tag = f"nav_{tab}"
        cv.delete(tag)
        cx, cy = self._nav_pos[tab], 54
        font = self._font(11, "bold")
        if tab == self._active_tab:
            for r, col in ((2, "#42340f"), (1, "#7a5c1d")):
                for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r),
                               (-r, -r), (r, -r), (-r, r), (r, r)):
                    cv.create_text(cx + dx, cy + dy, text=tab,
                                   font=font, fill=col, tags=tag)
            cv.create_text(cx, cy, text=tab, font=font, fill=C_GOLD_LT,
                           tags=tag)
        else:
            cv.create_text(cx, cy, text=tab, font=font,
                           fill=C_GOLD_LT if hover else C_TEXT, tags=tag)

        # Update-counter badges
        count = 0
        if tab == "MODS":
            count = self._mod_updates_count
        elif tab == "ADDONS":
            count = self._addon_updates_count
        if count:
            bx = cx + self._nav_text_w[tab] // 2 + 11
            by = cy - 11
            # Canvas oval, not a ● glyph: the oval gives exact geometric
            # control so the number always centers, consistently across OSes.
            # (A filled-circle glyph antialiases nicely but its disk isn't
            # centered in the glyph box — by a font-specific amount — so the
            # number drifts off-centre and can't be corrected reliably.)
            cv.create_oval(bx - 8, by - 8, bx + 8, by + 8,
                           fill=C_GOLD, outline="", tags=tag)
            cv.create_text(bx, by, text=str(count),
                           font=self._font(8, "bold"),
                           fill="#1a1408", tags=tag)

    def _switch_tab(self, tab: str):
        if tab == self._active_tab:
            return
        prev = self._active_tab
        self._active_tab = tab
        self._draw_nav_tab(prev)
        self._draw_nav_tab(tab)

        PANEL_TOP = HDR_H + 11

        # Panels stay mapped and stacked; switching tabs only raises the
        # active one. place_forget()/place() would unmap and remap the whole
        # widget tree of a populated panel (hundreds of widgets) every
        # switch — a visible synchronous stall.
        panels = {"NEWS":   self._news_panel,
                  "TWEAKS": self._tweaks_panel_frame,
                  "ADDONS": self._addons_panel_frame,
                  "MODS":   self._mods_panel_frame}
        target = panels.get(tab, self._news_panel)
        if not target.winfo_ismapped():
            x, y, w, h = panel_rect(self._win_w, self._win_h, top=PANEL_TOP)
            target.place(x=x, y=y, width=w, height=h)
        target.tkraise()
        self._active_panel = target

        if tab == "MODS":
            self._mods.load_latest_versions()
        elif tab == "TWEAKS":
            self._refresh_tweaks_panel()
        elif tab == "ADDONS":
            self._addons_verify()
        else:
            self._load_news()

    def _build(self):
        self._bg_canvas = tk.Canvas(self, width=self._win_w, height=self._win_h,
                                    bg=C_BG, highlightthickness=0)
        self._bg_canvas.place(x=0, y=0)
        self._draw_bg()

        self._build_header()
        self._build_panel()
        self._build_footer()

    def _on_resize(self, event):
        """Debounced relayout: recompute every placed container on resize."""
        if event.widget is not self:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._relayout)

    def _relayout(self):
        """Recompute geometry for all containers using the current size."""
        self._resize_job = None
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w < 50 or h < 50:   # not mapped yet — ignore initial Configure
            return
        self._win_w, self._win_h = w, h

        self._bg_canvas.configure(width=w, height=h)
        self._draw_bg()

        # Header
        self._hdr_canvas.configure(width=w)
        self._place_header()

        # Footer
        self._foot_frame.place(x=0, y=h - FOOT_H, width=w, height=FOOT_H)
        self._pb_frame.place(x=250, y=0, width=progress_width(w),
                             height=FOOT_H)
        self._pb_width = progress_width(w)
        self._draw_progress(self._pb_val)

        # Main panels — place all four so a resize while hidden still lands
        # on the right geometry for the next tab switch.
        top = HDR_H + 11
        for panel in (self._news_panel, self._tweaks_panel_frame,
                      self._mods_panel_frame, self._addons_panel_frame):
            x, y, pw, ph = panel_rect(w, h, top=top)
            panel.place(x=x, y=y, width=pw, height=ph)

        # News split columns.
        inner_w = w - PANEL_PAD * 2
        self._news_left_w, self._news_right_w = news_columns(inner_w)
        self._feat_frame.place(x=0, y=0, width=self._news_left_w,
                               relheight=1.0)
        self._ann_frame.place(x=self._news_left_w + 12, y=0,
                              width=self._news_right_w, relheight=1.0)

        # Refresh the active tab's content for the new width.
        self.after_idle(self._refresh_active_for_width)

    def _refresh_active_for_width(self):
        tab = getattr(self, "_active_tab", None)
        if tab == "NEWS":
            self._render_featured(self._news.state.featured)
            self._render_announcements(self._news.state.items)
        elif tab == "TWEAKS":
            self._refresh_tweaks_panel()
        elif tab == "ADDONS":
            self._render_addons()
        elif tab == "MODS":
            pass  # rows stretch via canvas width binding

    def _row_wrap(self) -> int:
        """Sensible wraplength for a row's description text, clamped to the
        current window so it never overflows on narrow windows."""
        return ui_metrics.clamp(self._win_w - PANEL_PAD * 2 - 260, 200, 520)

    def _draw_bg(self):
        c = self._bg_canvas
        bloom_cx, bloom_cy = self._win_w - 80, 80
        for i in range(40, 0, -1):
            r  = i * 9
            alpha_frac = (40 - i) / 40
            r_val = int(0x12 + alpha_frac * (0x2e - 0x12))
            g_val = int(0x0e + alpha_frac * (0x18 - 0x0e))
            b_val = int(0x1a + alpha_frac * (0x50 - 0x1a))
            col = f"#{r_val:02x}{g_val:02x}{b_val:02x}"
            c.create_oval(bloom_cx - r, bloom_cy - r,
                          bloom_cx + r, bloom_cy + r,
                          fill=col, outline="")

        c.create_line(0, self._win_h - 1, self._win_w, self._win_h - 1, fill=C_PANEL_BDR)

    def _build_header(self):
        hdr = tk.Canvas(self, width=self._win_w, height=HDR_H,
                        bg=C_BG, highlightthickness=0)
        hdr.place(x=0, y=0, width=self._win_w, height=HDR_H)
        self._hdr_canvas = hdr

        # Same corner bloom as the main background (identical coordinates
        # and colors) so the header blends seamlessly with the body instead
        # of sitting as a darker separated band.
        bloom_cx, bloom_cy = self._win_w - 80, 80
        for i in range(40, 0, -1):
            r = i * 9
            alpha_frac = (40 - i) / 40
            r_val = int(0x12 + alpha_frac * (0x2e - 0x12))
            g_val = int(0x0e + alpha_frac * (0x18 - 0x0e))
            b_val = int(0x1a + alpha_frac * (0x50 - 0x1a))
            col = f"#{r_val:02x}{g_val:02x}{b_val:02x}"
            hdr.create_oval(bloom_cx - r, bloom_cy - r,
                            bloom_cx + r, bloom_cy + r,
                            fill=col, outline="")

        import tkinter.font as tkfont

        self._logo_y = HDR_H // 2 - 6
        self._draw_logo()

        nav_font = tkfont.Font(family=_UI_FONT,
                               size=-self._ui.px(11), weight="bold")
        tabs = ["NEWS", "TWEAKS", "ADDONS", "MODS"]
        self._active_tab  = "NEWS"
        self._nav_pos     = {}
        self._nav_text_w  = {}
        self._hdr_regions = {}
        x = 240
        for tab in tabs:
            w = nav_font.measure(tab) + 36
            self._nav_pos[tab]     = x + w // 2
            self._nav_text_w[tab]  = nav_font.measure(tab)
            self._hdr_regions[tab] = (x, 0, x + w, HDR_H)
            x += w
            self._draw_nav_tab(tab)

        self._place_header()

        # The wordmark is a clickable header element too (opens the repo).
        lb = hdr.bbox("logo")
        if lb:
            self._hdr_regions["logo"] = (lb[0] - 4, lb[1] - 4,
                                         lb[2] + 6, lb[3] + 4)
            self._logo_cx = (lb[0] + lb[2]) // 2
        else:
            self._logo_cx = 127
        # "Update available!" label under the wordmark, shown once the daily
        # self-update check finds a newer release.
        self._update_available = False
        self._draw_update_label()

        self._hdr_hover = None
        hdr.bind("<Button-1>", self._on_hdr_click)
        hdr.bind("<Motion>",   self._on_hdr_motion)
        hdr.bind("<Leave>",    lambda e: self._on_hdr_motion(None))

        self._clear_wdb_var = tk.BooleanVar(
            value=bool(self._cfg.get("clear_wdb_on_launch", False)))
        self._close_on_launch_var = tk.BooleanVar(
            value=bool(self._cfg.get("close_on_launch", False)))
        self._auto_mods_var = tk.BooleanVar(
            value=bool(self._cfg.get("auto_install_mods", True)))
        self._auto_addons_var = tk.BooleanVar(
            value=bool(self._cfg.get("auto_install_addons", True)))
        # Close-time auto-install pending flags live in the SettingsController
        # (armed by _toggle_auto_*, consumed by _close_settings).

    def _place_header(self):
        """Reposition width-dependent header elements (gear, hit regions)."""
        if not getattr(self, "_hdr_canvas", None):
            return
        w = self._win_w
        self._hdr_canvas.configure(width=w)
        # Keep the gear clear of the nav tabs on narrow windows: clamp its
        # anchor so it never slides under the last tab.
        self._hdr_regions["gear"] = (w - 36, 2, w - 2, 34)
        self._draw_gear()

    def _draw_logo(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("logo")
        cv.create_text(24, self._logo_y, text="Octo Updater",
                       font=self._font(24, "bold"),
                       fill="#b478d9" if hover else "#9a5cbf",
                       anchor="w", tags="logo")

    def _draw_update_label(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("upd_label")
        self._hdr_regions.pop("update", None)
        if not self._update_available:
            return
        cv.create_text(self._logo_cx, self._logo_y + 26,
                       text="Update available!",
                       font=self._font(10, "bold"),
                       fill=C_GOLD_LT if hover else C_GOLD,
                       anchor="n", tags="upd_label")
        lb = cv.bbox("upd_label")
        if lb:
            self._hdr_regions["update"] = (lb[0] - 4, lb[1] - 2,
                                           lb[2] + 4, lb[3] + 2)

    def _draw_gear(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("gear_icon")
        cv.create_text(self._win_w - 10, 8, text="⚙", font=self._font(13),
                       fill=C_GOLD if hover else C_TEXT_DIM,
                       anchor="ne", tags="gear_icon")

    def _hdr_hit(self, x, y):
        for name, (x0, y0, x1, y1) in self._hdr_regions.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _on_hdr_motion(self, event):
        name = self._hdr_hit(event.x, event.y) if event is not None else None
        if name == self._hdr_hover:
            return
        prev = self._hdr_hover
        self._hdr_hover = name
        if prev in self._nav_pos:
            self._draw_nav_tab(prev)
        if name in self._nav_pos:
            self._draw_nav_tab(name, hover=True)
        if "gear" in (prev, name):
            self._draw_gear(hover=(name == "gear"))
        if "logo" in (prev, name):
            self._draw_logo(hover=(name == "logo"))
        if "update" in (prev, name):
            self._draw_update_label(hover=(name == "update"))
        self._hdr_canvas.configure(cursor="hand2" if name else "")

    def _on_hdr_click(self, event):
        name = self._hdr_hit(event.x, event.y)
        if name == "gear":
            self._open_settings(event)
        elif name in ("logo", "update"):
            self._open_url("https://github.com/rebasedkon/octo-updater")
        elif name in self._nav_pos:
            self._switch_tab(name)

    def _build_panel(self):
        PANEL_TOP  = HDR_H + 1
        PANEL_H    = self._win_h - PANEL_TOP - FOOT_H
        PAD        = PANEL_PAD

        panel = tk.Frame(self, bg=C_BG)
        x, y, w, h = panel_rect(self._win_w, self._win_h, top=PANEL_TOP + 10)
        panel.place(x=x, y=y, width=w, height=h)
        self._news_panel = panel
        self._active_panel = panel   # NEWS is the initial tab

        inner_w = w
        self._news_left_w, self._news_right_w = news_columns(inner_w)

        # Featured forum post — parchment panel (left)
        feat = tk.Frame(panel, bg=C_PARCH)
        feat.place(x=0, y=0, width=self._news_left_w, relheight=1.0)
        self._feat_frame = feat

        # Announcements list (right)
        ann = tk.Frame(panel, bg=C_PANEL,
                       highlightthickness=1,
                       highlightbackground=C_PANEL_BDR)
        ann.place(x=self._news_left_w + 12, y=0,
                  width=self._news_right_w, relheight=1.0)
        self._ann_frame = ann

        self._render_featured(None, loading=True)
        self._render_announcements(None, loading=True)

        self._log_line("Octo Updater  v" + UPDATER_VERSION + "\n", "acct")
        self._log_line("─" * 60 + "\n", "dim")
        self._build_mods_panel()
        self._build_tweaks_panel()
        self._build_addons_panel()

    # ── news panel ───────────────────────────────────────────────────────────

    def _load_news(self, force=False):
        self._news.load(force)

    def _load_featured(self, force=False):
        self._news.refresh_featured(force)

    def _load_announcements(self, force=False):
        self._news.refresh_announcements(force)

    def _render_featured(self, post, loading=False, error=""):
        f = self._feat_frame
        for w in f.winfo_children():
            w.destroy()
        f.configure(highlightthickness=1, highlightbackground=C_PARCH_EDGE)

        title = (post or {}).get("title", "")

        # Title band — slightly darker parchment strip
        band = tk.Frame(f, bg=C_PARCH_BAND)
        band.pack(fill="x")
        hdr = tk.Frame(band, bg=C_PARCH_BAND)
        hdr.pack(fill="x", padx=20, pady=(16, 12))
        tk.Label(hdr,
                 text=title.upper() if title else "NEWS",
                 font=self._font(13, "bold"),
                 fg=C_PARCH_TITLE, bg=C_PARCH_BAND,
                 wraplength=self._news_left_w - 100,
                 justify="left", anchor="w").pack(side="left",
                                                  fill="x", expand=True)
        rf = tk.Label(hdr, text="⟳", font=self._font(14),
                      fg=C_PARCH_DIM, bg=C_PARCH_BAND, cursor="hand2")
        rf.pack(side="right")
        rf.bind("<Button-1>", lambda e: self._load_featured(force=True))
        rf.bind("<Enter>",    lambda e: rf.configure(fg=C_PARCH_LINK))
        rf.bind("<Leave>",    lambda e: rf.configure(fg=C_PARCH_DIM))

        if not post:
            msg = error or ("Loading…" if loading
                            else "No news yet — check back later.")
            tk.Label(f, text=msg, font=self._font(10),
                     fg=C_PARCH_DIM, bg=C_PARCH).pack(padx=20, pady=16,
                                                      anchor="w")
            return

        byline = []
        if post.get("author"):
            byline.append(f"by {post['author']}")
        byline.append(_format_news_date(post.get("date", "")))
        bl = tk.Frame(f, bg=C_PARCH_BAND)
        bl.pack(fill="x")
        tk.Label(bl, text=" · ".join(byline),
                 font=self._font(10, "italic"),
                 fg=C_PARCH_DIM, bg=C_PARCH_BAND,
                 anchor="w").pack(fill="x", padx=20, pady=10)
        tk.Frame(f, bg=C_PARCH_LINE, height=1).pack(fill="x")

        # Pack the link first with side="bottom" so it's always reserved its
        # space; the body Text (which defaults to 24 lines tall) then fills
        # only the remaining area instead of clipping the link off the panel.
        if post.get("url"):
            link = tk.Label(f, text="⧉  Read full post on the forum",
                            font=self._font(11),
                            fg=C_PARCH_LINK, bg=C_PARCH,
                            cursor="hand2", anchor="w")
            link.pack(side="bottom", fill="x", padx=20, pady=(4, 16))
            link.bind("<Button-1>",
                      lambda e, u=post["url"]: self._open_url(u))
            link.bind("<Enter>", lambda e: link.configure(fg=C_PARCH_TITLE))
            link.bind("<Leave>", lambda e: link.configure(fg=C_PARCH_LINK))

        body = _strip_html(post.get("html", ""))
        txt = tk.Text(f, bg=C_PARCH, fg=C_PARCH_TEXT, relief="flat",
                      font=self._font(11), wrap="word", height=1,
                      padx=2, pady=8, spacing2=4, spacing3=4,
                      highlightthickness=0, cursor="arrow")
        txt.insert("1.0", body)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=20, pady=(8, 2))

    def _render_announcements(self, items, loading=False, error=""):
        f = self._ann_frame
        for w in f.winfo_children():
            w.destroy()

        hdr = tk.Frame(f, bg=C_PANEL)
        hdr.pack(fill="x", padx=14, pady=(16, 10))
        tk.Label(hdr, text="ANNOUNCEMENTS",
                 font=self._font(12, "bold"),
                 fg=C_GOLD, bg=C_PANEL).pack(side="left")
        rf = tk.Label(hdr, text="⟳", font=self._font(14),
                      fg=C_TEXT_DIM, bg=C_PANEL, cursor="hand2")
        rf.pack(side="right")
        rf.bind("<Button-1>", lambda e: self._load_announcements(force=True))
        rf.bind("<Enter>",    lambda e: rf.configure(fg=C_GOLD))
        rf.bind("<Leave>",    lambda e: rf.configure(fg=C_TEXT_DIM))

        tk.Frame(f, bg=C_DIVIDER, height=1).pack(fill="x", padx=14)

        if items is None or error:
            msg = error or ("Loading…" if loading
                            else "Couldn't reach the news feed.")
            tk.Label(f, text=msg, font=self._font(9),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(padx=14, pady=12,
                                                     anchor="w")
            return
        if not items:
            tk.Label(f, text="No news yet — check back later.",
                     font=self._font(9), fg=C_TEXT_DIM,
                     bg=C_PANEL).pack(padx=14, pady=12, anchor="w")
            return

        list_frame = tk.Frame(f, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True, padx=(14, 4), pady=(0, 10))
        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL)
        self._wheel_canvases.append(canvas)
        inner = tk.Frame(canvas, bg=C_PANEL)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw",
                             width=self._news_right_w - 40)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        wrap_w = self._news_right_w - 50
        for item in items:
            top = tk.Frame(inner, bg=C_PANEL)
            top.pack(fill="x", pady=(12, 0))
            tk.Label(top, text=_format_news_date(item.get("date", "")),
                     font=self._font(9), fg=C_TEXT_DIM,
                     bg=C_PANEL).pack(side="right", anchor="n")
            tk.Label(top, text=item.get("title", ""),
                     font=self._font(11, "bold"),
                     fg=C_GOLD, bg=C_PANEL,
                     wraplength=wrap_w - 85, justify="left",
                     anchor="w").pack(side="left", fill="x", expand=True)

            if item.get("author"):
                tk.Label(inner, text=f"by {item['author']}",
                         font=self._font(10, "italic"),
                         fg=C_TEXT_DIM, bg=C_PANEL,
                         anchor="w").pack(fill="x", pady=(2, 0))

            body = item.get("body", "").strip()
            if len(body) > 260:
                body = body[:260].rstrip() + "…"
            if body:
                tk.Label(inner, text=body, font=self._font(10),
                         fg=C_TEXT, bg=C_PANEL,
                         wraplength=wrap_w, justify="left",
                         anchor="w").pack(fill="x", pady=(5, 0))

            if item.get("url"):
                lnk = tk.Label(inner, text="⧉ Read more",
                               font=self._font(10),
                               fg=C_GOLD, bg=C_PANEL,
                               cursor="hand2", anchor="w")
                lnk.pack(fill="x", pady=(5, 0))
                lnk.bind("<Button-1>",
                         lambda e, u=item["url"]: self._open_url(u))
                lnk.bind("<Enter>", lambda e, w=lnk: w.configure(fg=C_GOLD_LT))
                lnk.bind("<Leave>", lambda e, w=lnk: w.configure(fg=C_GOLD))

            tk.Frame(inner, bg=C_DIVIDER, height=1).pack(fill="x",
                                                         pady=(12, 0))

    # ── tweaks panel ─────────────────────────────────────────────────────────────

    def _build_tweaks_panel(self):
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR,
                         highlightcolor=C_PANEL_BDR)
        self._tweaks_panel_frame = outer

        self._tweaks_inner = tk.Frame(outer, bg=C_PANEL)
        self._tweaks_inner.pack(fill="both", expand=True)

        tk.Frame(outer, bg=C_DIVIDER, height=1).pack(fill="x", padx=16)
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(fill="x", padx=16, pady=(6, 10))

        # Packed on demand by _refresh_tweaks_buttons(): Apply appears only
        # when UI values differ from the saved config, Reset only when the
        # saved values differ from the defaults.
        apl = tk.Label(foot, text="Apply", font=self._font(11),
                       fg=C_TEXT, bg=C_PANEL_BDR, cursor="hand2",
                       padx=16, pady=4)
        apl.bind("<Button-1>", lambda e: self._apply_tweaks())
        apl.bind("<Enter>",    lambda e: apl.configure(bg=C_GOLD, fg="#000"))
        apl.bind("<Leave>",    lambda e: apl.configure(bg=C_PANEL_BDR, fg=C_TEXT))
        self._tweaks_apply_btn = apl

        rst = tk.Label(foot, text="Reset", font=self._font(11),
                       fg=C_TEXT, bg=C_PANEL_BDR, cursor="hand2",
                       padx=16, pady=4)
        rst.bind("<Button-1>", lambda e: self._reset_tweaks())
        rst.bind("<Enter>",    lambda e: rst.configure(bg=C_GOLD, fg="#000"))
        rst.bind("<Leave>",    lambda e: rst.configure(bg=C_PANEL_BDR, fg=C_TEXT))
        self._tweaks_reset_btn = rst

        self._tweak_widgets: dict = {}
        self._tweak_vars:    dict = {}

        self._build_tweaks_rows()

    def _build_tweaks_rows(self):
        for w in self._tweaks_inner.winfo_children():
            w.destroy()
        self._tweak_widgets = {}
        self._tweak_vars    = {}

        values = load_tweaks_config()
        PAD_X  = 16

        for (tid, label, kind, recommended, _, desc, mn, mx, step) in TWEAKS_ITEMS:
            if kind == "section":
                tk.Label(self._tweaks_inner, text=label,
                         font=self._font(11, "bold"),
                         fg=C_GOLD, bg=C_PANEL,
                         anchor="w").pack(fill="x", padx=PAD_X, pady=(10, 2))
                tk.Frame(self._tweaks_inner, bg=C_DIVIDER, height=1).pack(
                    fill="x", padx=PAD_X, pady=(0, 4))
                continue

            row = tk.Frame(self._tweaks_inner, bg=C_PANEL)
            row.pack(fill="x", padx=PAD_X, pady=3)

            tk.Label(row, text=label,
                     font=self._font(10, "bold"),
                     fg=C_TEXT, bg=C_PANEL,
                     width=22, anchor="w").pack(side="left")

            if kind == "checkbox":
                var = tk.BooleanVar(value=values.get(tid, False))
                var.trace_add("write", self._refresh_tweaks_buttons)
                tk.Checkbutton(row, variable=var,
                               bg=C_PANEL, activebackground=C_PANEL,
                               fg=C_TEXT, selectcolor=C_PANEL,
                               highlightthickness=0, bd=0,
                               relief="flat", cursor="hand2"
                               ).pack(side="left", padx=(4, 12))
                self._tweak_vars[tid] = var

            elif kind == "number":
                val = values.get(tid, mn or 0)
                var = tk.StringVar(value=str(int(val)))
                var.trace_add("write", self._refresh_tweaks_buttons)
                entry = tk.Entry(row, textvariable=var,
                                 bg="#18181e", fg=C_TEXT,
                                 insertbackground=C_GOLD,
                                 relief="flat", font=self._mono(9),
                                 width=7,
                                 highlightthickness=1,
                                 highlightbackground=C_PANEL_BDR,
                                 highlightcolor=C_GOLD,
                                 justify="center")
                entry.pack(side="left", padx=(4, 12), ipady=3)
                self._tweak_vars[tid]   = var
                self._tweak_widgets[tid] = entry

                def _clamp(e, t=tid, lo=mn, hi=mx):
                    try:
                        v = int(float(self._tweak_vars[t].get()))
                        if lo is not None: v = max(lo, v)
                        if hi is not None: v = min(hi, v)
                        self._tweak_vars[t].set(str(v))
                    except ValueError:
                        self._tweak_vars[t].set(str(TWEAKS_DEFAULTS.get(t, lo or 0)))
                entry.bind("<FocusOut>", _clamp)
                entry.bind("<Return>",   _clamp)

            if desc:
                tk.Label(row, text=desc,
                         font=self._font(10), fg=C_TEXT_DIM, bg=C_PANEL,
                         wraplength=self._row_wrap(), justify="left",
                         anchor="w"
                         ).pack(side="left", fill="x", expand=True)

        self._refresh_tweaks_buttons()

    def _refresh_tweaks_buttons(self, *args):
        """Show Apply only when the UI differs from the saved config and
        Reset only when values are custom (differ from the defaults); paint
        out-of-range number entries red."""
        if not getattr(self, "_tweak_vars", None):
            return

        any_bad = False
        for tid, entry in self._tweak_widgets.items():
            lo, hi = TWEAKS_LIMITS.get(tid, (None, None))
            try:
                v = int(float(self._tweak_vars[tid].get()))
                bad = ((lo is not None and v < lo) or
                       (hi is not None and v > hi))
            except ValueError:
                bad = True
            any_bad = any_bad or bad
            entry.configure(fg=C_ERR if bad else C_TEXT)

        ui       = self._get_tweaks_from_ui()
        saved    = load_tweaks_config()
        defaults = dict(TWEAKS_DEFAULTS)
        defaults["fieldOfView"] = fov_default_for_display()

        def norm(d):
            return {k: (bool(d.get(k))
                        if isinstance(TWEAKS_DEFAULTS.get(k), bool)
                        else int(d.get(k, 0)))
                    for k in ui}

        # An out-of-range entry always counts as a change: _get_tweaks_from_ui
        # clamps it, and the clamped value can coincide with the saved one
        # (e.g. saved 180, typed 192 → clamps to 180), which would otherwise
        # hide the buttons while the entry still shows an invalid number.
        ui_n   = norm(ui)
        dirty  = any_bad or ui_n != norm(saved)
        custom = any_bad or ui_n != norm(defaults)

        self._tweaks_apply_btn.pack_forget()
        self._tweaks_reset_btn.pack_forget()
        if dirty:
            self._tweaks_apply_btn.pack(side="left")
        if custom:
            self._tweaks_reset_btn.pack(side="left",
                                        padx=(8, 0) if dirty else (0, 0))

    def _refresh_tweaks_panel(self):
        values = load_tweaks_config()
        for tid, var in self._tweak_vars.items():
            v = values.get(tid, TWEAKS_DEFAULTS.get(tid))
            if isinstance(var, tk.BooleanVar):
                var.set(bool(v))
            else:
                var.set(str(int(v)) if v is not None else "")

    def _get_tweaks_from_ui(self) -> dict:
        """Read tweak values from the UI, always clamped to their limits —
        an out-of-range entry can never reach the config or the exe patch."""
        result = {}
        for tid, var in self._tweak_vars.items():
            if isinstance(var, tk.BooleanVar):
                result[tid] = var.get()
            else:
                try:
                    v = int(float(var.get()))
                except ValueError:
                    v = TWEAKS_DEFAULTS.get(tid, 0)
                lo, hi = TWEAKS_LIMITS.get(tid, (None, None))
                if lo is not None:
                    v = max(lo, v)
                if hi is not None:
                    v = min(hi, v)
                result[tid] = v
        return result

    def _reset_tweaks(self):
        defaults = dict(TWEAKS_DEFAULTS)
        defaults["fieldOfView"] = fov_default_for_display()
        save_tweaks_config(defaults)
        self._refresh_tweaks_panel()
        out = self._path_var.get().strip()
        # On Windows Config.wtf tweaks apply alongside the WoW.exe patch; on
        # other platforms only the (existing) game folder is required.
        if can_patch_client():
            needs_exe = os.path.exists(os.path.join(out, "WoW.exe"))
        else:
            needs_exe = bool(out)
        if needs_exe:
            self._set_btn_busy("Patching…")
            self._status_var.set("Applying tweaks…")
            # Pass the same defaults that were saved
            run_apply_worker_in_background(self._apply_tweaks_worker,
                                           out, defaults)

    def _apply_tweaks(self):
        values = self._get_tweaks_from_ui()
        save_tweaks_config(values)
        # Write the (possibly clamped) saved values back into the entries so
        # the UI never keeps showing an out-of-range number after Apply.
        self._refresh_tweaks_panel()

        out = self._path_var.get().strip()
        if not out:
            self._log_line("Game folder not set.\n", "err")
            return

        exe = os.path.join(out, "WoW.exe")
        if can_patch_client() and not os.path.exists(exe):
            self._log_line("WoW.exe not found — run Update first.\n", "err")
            return

        self._log_line("\nApplying tweaks to WoW.exe...\n", "acct")
        self._set_btn_busy("Patching…")
        self._status_var.set("Applying tweaks…")
        run_apply_worker_in_background(self._apply_tweaks_worker,
                                       out, values)

    def _apply_tweaks_worker(self, client_dir: str, tweaks: dict):
        log_q  = queue.Queue()
        prog_q = queue.Queue()
        worker = UpdateWorker(client_dir, log_q, prog_q)

        def drain():
            try:
                while True:
                    msg, tag = log_q.get_nowait()
                    if msg not in ("__DONE__", "__ERROR__") and not msg.startswith("__"):
                        self.after(0, lambda m=msg, t=tag: self._log_line(
                            (m if m.endswith("\n") else m + "\n"), t))
            except queue.Empty:
                pass

        try:
            exe_path = os.path.join(client_dir, "WoW.exe")

            fresh_cfg        = load_config()
            expected_patched = fresh_cfg.get("expected_patched_wow_hash", "")
            original_server  = fresh_cfg.get("original_server_wow_hash", "")
            local_before     = sha1_file(exe_path) if os.path.exists(exe_path) else ""

            if can_patch_client():
                worker.patch_exe(tweaks)
                drain()
            else:
                # Binary tweaks target Windows offsets — on other platforms
                # only the Config.wtf settings are applied.
                self._log_line(
                    "Binary WoW.exe tweaks are only applied on Windows; "
                    "writing Config.wtf only.\n", "dim")

            update_config_wtf(client_dir, tweaks)

            local_after = sha1_file(exe_path) if os.path.exists(exe_path) else ""

            def _set_hashes(c):
                c["expected_patched_wow_hash"] = local_after
                if local_before == expected_patched and original_server:
                    c["original_server_wow_hash"] = original_server
                else:
                    c.pop("original_server_wow_hash", None)
            self._cfg = update_config(_set_hashes)

            self._log_line("\nTweaks applied.\n", "ok")
            self.after(0, self._refresh_ready_state)
        except Exception as e:
            drain()
            self._log_line(f"\n✗ Tweak patch failed: {e}\n", "err")

            def _fail_state():
                self._status_var.set("Tweaks failed — check the log")
                self._set_btn_update()
            self.after(0, _fail_state)

    # ── mods panel ───────────────────────────────────────────────────────────────

    def _build_mods_panel(self):
        PAD = 18

        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR)
        self._mods_panel_frame = outer

        note = tk.Frame(outer, bg=C_PANEL)
        note.pack(fill="x", padx=16, pady=(14, 8), anchor="w")
        tk.Label(note, text="Mods marked with ",
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")
        tk.Label(note, text="★", font=self._font(10),
                 fg=C_GOLD, bg=C_PANEL).pack(side="left")
        tk.Label(note, text=" are essential",
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")

        tk.Frame(outer, bg=C_DIVIDER, height=1).pack(fill="x", padx=16, pady=(0, 4))

        list_frame = tk.Frame(outer, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True, padx=16)

        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL)
        self._wheel_canvases.append(canvas)
        self._mods_inner = tk.Frame(canvas, bg=C_PANEL)
        self._mods_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        mods_win = canvas.create_window((0, 0), window=self._mods_inner,
                                        anchor="nw")
        # Stretch rows to the full canvas width so right-side controls
        # (Ignore updates) sit flush against the scrollbar.
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(mods_win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        tk.Frame(outer, bg=C_DIVIDER, height=1).pack(fill="x", padx=16, pady=(4, 0))
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(fill="x", padx=16, pady=(6, 10))

        # Packed on demand by _refresh_apply_btn_visibility(): shown only
        # when there are unapplied checkbox changes or a mod is in error.
        self._apply_btn = tk.Label(foot, text="Apply",
                                   font=self._font(11),
                                   fg=C_TEXT, bg=C_PANEL_BDR,
                                   cursor="hand2", padx=16, pady=4)
        self._apply_btn.bind("<Button-1>", lambda e: self._apply_mods())
        self._apply_btn.bind("<Enter>",    lambda e: self._apply_btn.configure(bg=C_GOLD, fg="#000"))
        self._apply_btn.bind("<Leave>",    lambda e: self._apply_btn.configure(bg=C_PANEL_BDR, fg=C_TEXT))

        self._mod_row_vars: dict = {}
        self._render_mod_rows()
        self._refresh_apply_btn_visibility()

    def _render_mod_rows(self):
        records = self._mods.state.records
        pending = self._mods.state.pending
        latest  = self._mods.state.latest_versions

        mods_sorted = sorted(self._mods.registry,
                             key=lambda m: m["name"].lower())

        if self._mod_row_vars:
            for mod in mods_sorted:
                mid   = mod["id"]
                rec   = records.get(mid)
                refs  = self._mod_row_vars.get(mid, {})
                if not refs:
                    continue

                installed_version = rec.installed_version if rec else None
                enabled = rec.enabled if rec else False
                ignore_updates = rec.ignore_updates if rec else False
                has_error = rec.error if rec else None
                installed = bool(installed_version)

                if "ver_label" in refs:
                    # Installed mods show their installed version; others show
                    # the latest available.
                    ver = installed_version or latest.get(mid) or "unknown"
                    refs["ver_label"].configure(text=f"  {ver}")

                # Checkbox always reflects config only — never a registry default.
                # A mod only shows checked if it's actually recorded as installed.
                if mid not in pending:
                    if "enabled" in refs:
                        refs["enabled"].set(enabled)
                    if "ignore" in refs:
                        refs["ignore"].set(ignore_updates)

                if "name_label" in refs:
                    refs["name_label"].configure(
                        fg=C_ERR if has_error else (C_MOD_HL if installed else C_TEXT))
                if "desc_label" in refs:
                    refs["desc_label"].configure(
                        fg=C_TEXT if enabled else C_TEXT_DIM)
                if "error_label" in refs:
                    if has_error:
                        refs["error_label"].configure(text=f"  \u26a0  {has_error}")
                        refs["error_label"].pack(fill="x", pady=(0, 4))
                    else:
                        refs["error_label"].pack_forget()

                if "update_label" in refs:
                    self._style_mod_action_label(refs["update_label"], mod)
            return

        for w in self._mods_inner.winfo_children():
            w.destroy()
        self._mod_row_vars = {}

        for mod in mods_sorted:
            mid   = mod["id"]
            rec   = records.get(mid)
            pend  = pending.get(mid)
            installed_version = rec.installed_version if rec else None
            # Checkbox reflects only what's actually recorded in config — never
            # a registry default. Pending (not-yet-applied) UI changes still
            # win so an in-progress toggle survives a background re-render.
            enabled = (pend.enabled
                       if pend is not None and pend.enabled is not None
                       else (rec.enabled if rec else False))
            ignore_upd = rec.ignore_updates if rec else False
            has_error  = rec.error if rec else None
            essential  = mod.get("essential", False)
            installed  = bool(installed_version)
            # Installed mods are highlighted green; the name is neutral text
            # otherwise (error state overrides to red in the refresh paths).
            name_col   = C_MOD_HL if installed else C_TEXT

            # Installed mods show their installed version; others show latest.
            latest_ver = installed_version or latest.get(mid) or "unknown"

            container = tk.Frame(self._mods_inner, bg=C_PANEL)
            container.pack(fill="x")

            row = tk.Frame(container, bg=C_PANEL)
            row.pack(fill="x", pady=5)

            name_f = tk.Frame(row, bg=C_PANEL, width=210)
            name_f.pack(side="left", fill="y")
            name_f.pack_propagate(False)
            # Essential mods get a gold star badge; a fixed-width slot keeps
            # the names aligned whether or not the star is present.
            star = tk.Label(name_f, text="★" if essential else "",
                            font=self._font(9), fg=C_GOLD, bg=C_PANEL,
                            width=2, anchor="w")
            star.pack(side="left")
            if essential:
                self._add_tooltip(star, "Essential mod")
            name_label = tk.Label(name_f, text=mod["name"],
                                  font=self._font(10, "bold"),
                                  fg=name_col, bg=C_PANEL, anchor="w")
            name_label.pack(side="left")
            ver_label = tk.Label(name_f, text=f"  {latest_ver}",
                                 font=self._font(9), fg=C_TEXT_DIM, bg=C_PANEL)
            ver_label.pack(side="left")

            enabled_var = tk.BooleanVar(value=enabled)
            tk.Checkbutton(row, variable=enabled_var,
                           bg=C_PANEL, activebackground=C_PANEL,
                           fg=C_TEXT, selectcolor=C_PANEL,
                           highlightthickness=0, bd=0,
                           relief="flat", cursor="hand2",
                           command=lambda m=mid, v=enabled_var: self._toggle_mod(m, v)
                           ).pack(side="left", padx=(4, 8))

            # Right-side widgets are packed first so they stay pinned to the
            # panel's right edge; the description then fills the middle.
            ignore_var = tk.BooleanVar(value=ignore_upd)
            ig_f = tk.Frame(row, bg=C_PANEL)
            ig_f.pack(side="right", padx=(8, 0))
            tk.Checkbutton(ig_f, variable=ignore_var,
                           bg=C_PANEL, activebackground=C_PANEL,
                           fg=C_TEXT, selectcolor=C_PANEL,
                           highlightthickness=0, bd=0,
                           relief="flat", cursor="hand2",
                           command=lambda m=mid, v=ignore_var: self._set_ignore(m, v)
                           ).pack(side="left")
            tk.Label(ig_f, text="Ignore updates",
                     font=self._font(9), fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")

            link = tk.Label(row, text="⧉",
                            font=self._font(12), fg=C_TEXT_DIM,
                            bg=C_PANEL, cursor="hand2")
            link.pack(side="right", padx=4)
            link.bind("<Button-1>", lambda e, u=mod["repo_url"]: self._open_url(u))
            link.bind("<Enter>",    lambda e, l=link: l.configure(fg=C_GOLD))
            link.bind("<Leave>",    lambda e, l=link: l.configure(fg=C_TEXT_DIM))

            update_label = tk.Label(row, text="update",
                                    font=self._font(10, "bold"),
                                    fg=C_GOLD, bg=C_PANEL, cursor="hand2")
            update_label.bind("<Button-1>", lambda e, m=mid: self._update_mod(m))
            update_label.bind("<Enter>", lambda e, l=update_label:
                              l.configure(fg=getattr(l, "_hover", C_GOLD_LT)))
            update_label.bind("<Leave>", lambda e, l=update_label:
                              l.configure(fg=getattr(l, "_base", C_GOLD)))
            self._style_mod_action_label(update_label, mod)

            desc_label = tk.Label(row, text=mod["description"],
                                  font=self._font(10),
                                  fg=(C_TEXT if enabled else C_TEXT_DIM),
                                  bg=C_PANEL, wraplength=self._row_wrap(),
                                  justify="left", anchor="w")
            desc_label.pack(side="left", fill="x", expand=True)

            error_label = tk.Label(container, text="",
                                   font=self._font(9), fg=C_ERR,
                                   bg=C_PANEL, anchor="w", padx=16)
            if has_error:
                name_label.configure(fg=C_ERR)
                error_label.configure(text=f"  \u26a0  {has_error}")
                error_label.pack(fill="x", pady=(0, 4))

            divider = tk.Frame(self._mods_inner, bg=C_DIVIDER, height=1)
            divider.pack(fill="x", pady=(2, 0))

            self._mod_row_vars[mid] = {
                "enabled":      enabled_var,
                "ignore":       ignore_var,
                "ver_label":    ver_label,
                "name_label":   name_label,
                "desc_label":   desc_label,
                "error_label":  error_label,
                "update_label": update_label,
            }

    def _refresh_mods_badge(self):
        try:
            count = self._mods.updates_count
        except Exception:
            count = 0
        if count != self._mod_updates_count:
            self._mod_updates_count = count
            self._draw_nav_tab("MODS")

    def _toggle_mod(self, mod_id: str, var: tk.BooleanVar):
        self._mods.toggle(mod_id, var.get())
        self._refresh_apply_btn_visibility()

    def _set_ignore(self, mod_id: str, var: tk.BooleanVar):
        self._mods.set_ignore(mod_id, var.get())
        self._refresh_apply_btn_visibility()

    def _refresh_apply_btn_visibility(self):
        """Apply is offered only when there is something to apply: pending
        checkbox changes, or a failed mod the user may want to retry."""
        if self._mods.state.has_pending_changes or self._mods.state.has_errors:
            if not self._apply_btn.winfo_ismapped():
                self._apply_btn.pack(side="left")
        else:
            self._apply_btn.pack_forget()

    def _open_url(self, url: str):
        self._settings.open_url(url)

    def _style_mod_action_label(self, lbl, mod):
        """Drive the per-mod action label: 'retry' (red) when the mod is in
        an error state, 'update' (gold) when a newer version is available,
        hidden otherwise. Both do the same thing — reinstall the mod."""
        action = self._mods.action_for(mod["id"])
        if action in ("retry", "update"):
            lbl._base, lbl._hover = C_GOLD, C_GOLD_LT
            lbl.configure(text=action, fg=C_GOLD)
            lbl.pack(side="right", padx=(2, 8))
        else:
            lbl.pack_forget()

    def _update_mod(self, mod_id: str):
        """Download and install the newest release of a single mod (the
        per-row "update" label). Runs through the normal apply worker so
        errors/versions are recorded exactly like a manual Apply."""
        out = self._path_var.get().strip()
        if not out:
            return
        mod = next(m for m in self._mods.registry if m["id"] == mod_id)
        self._log_line(f"\nUpdating {mod['name']}...\n", "acct")
        self._set_btn_busy("Installing…")
        self._status_var.set("Downloading mods…")
        self._mods.apply(only_mod_id=mod_id)

    def _apply_mods(self):
        out = self._path_var.get().strip()
        if not out:
            return
        self._apply_btn.configure(text="Applying...", bg="#2a2a32", fg=C_TEXT_DIM)
        self._set_btn_busy("Installing…")
        self._status_var.set("Downloading mods…")
        self._mods.apply()

    def _maybe_install_default_addons(self):
        """Thin forwarder — the one-shot recommended-addons auto-install
        lives in the AddonsController (same fresh-folder mechanism as the
        default mods); this only kicks the panel chrome for whichever flow —
        batch install or plain verify — actually started."""
        if not self._addons.maybe_install_default_addons():
            return
        if self._addons.state.installing:
            self._render_addons()
            self._set_btn_busy("Installing…")
        elif self._addons.state.addons or self._addons.state.available:
            self._refresh_addons_footer()
        else:
            self._render_addons()

    def _maybe_install_essential_mods(self):
        """Auto-install every mod flagged essential the first time this game
        folder is ready to use — i.e. on a brand-new install, or right after
        the game folder was changed to a new location. The one-shot guard,
        the pending seed and the apply worker live in the ModsController."""
        if self._mods.maybe_install_essential_mods():
            self._set_btn_busy("Installing…")

    # ── addons panel ─────────────────────────────────────────────────────────

    def _build_addons_panel(self):
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR,
                         highlightcolor=C_PANEL_BDR)
        self._addons_panel_frame = outer

        top = tk.Frame(outer, bg=C_PANEL)
        top.pack(fill="x", padx=16, pady=(4, 0))
        self._addon_filter_var = tk.StringVar()
        self._addon_filter_job = None
        self._addon_filter_var.trace_add(
            "write", self._on_addon_filter_changed)
        ent = tk.Entry(top, textvariable=self._addon_filter_var,
                       bg="#2b2244", fg=C_TEXT, insertbackground=C_GOLD,
                       relief="flat", font=self._font(10), width=24,
                       highlightthickness=1,
                       highlightbackground="#4a3c6e",
                       highlightcolor=C_GOLD)
        tk.Label(top, text="⌕", font=self._font(18),
                 fg=C_TEXT, bg=C_PANEL).pack(side="right")
        ent.pack(side="right", ipady=4, padx=(0, 6))

        legend = tk.Frame(top, bg=C_PANEL)
        legend.pack(side="left")
        tk.Label(legend, text="Addons marked with ",
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")
        tk.Label(legend, text="★", font=self._font(10),
                 fg=C_GOLD, bg=C_PANEL).pack(side="left")
        tk.Label(legend, text=" are recommended",
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")

        list_frame = tk.Frame(outer, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True, padx=(16, 4))
        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL)
        self._wheel_canvases.append(canvas)
        self._addons_canvas = canvas
        self._addons_win    = None
        canvas.bind("<Configure>",
                    lambda e: self._addons_win is not None
                    and canvas.itemconfigure(self._addons_win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._reset_addons_inner()

        tk.Frame(outer, bg=C_DIVIDER, height=1).pack(fill="x", padx=16,
                                                     pady=(4, 0))
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(fill="x", padx=16, pady=(8, 12))
        foot.columnconfigure(0, weight=1)
        foot.columnconfigure(1, weight=1)
        foot.columnconfigure(2, weight=1)

        chk = tk.Label(foot, text="⟳  Check for updates",
                       font=self._font(10), fg=C_TEXT_DIM, bg=C_PANEL,
                       cursor="hand2")
        chk.grid(row=0, column=0, sticky="w")
        chk.bind("<Button-1>", lambda e: self._addons_verify(force=True))
        chk.bind("<Enter>", lambda e: chk.configure(fg=C_GOLD))
        chk.bind("<Leave>", lambda e: chk.configure(fg=C_TEXT_DIM))

        add = tk.Label(foot, text="+  Add custom git addon",
                       font=self._font(10, "bold"), fg="#d76f9e",
                       bg=C_PANEL, cursor="hand2")
        add.grid(row=0, column=1)
        add.bind("<Button-1>", lambda e: self._open_custom_addon_dialog())
        add.bind("<Enter>", lambda e: add.configure(fg="#eb96ba"))
        add.bind("<Leave>", lambda e: add.configure(fg="#d76f9e"))

        self._addons_right_lbl = tk.Label(foot, text="",
                                          font=self._font(10, "bold"),
                                          bg=C_PANEL, cursor="hand2")
        self._addons_right_lbl.grid(row=0, column=2, sticky="e")
        self._addons_right_lbl.bind("<Button-1>",
                                    lambda e: self._addon_update_all())

        self._render_addons()

    # ── addons engine (app side) ─────────────────────────────────────────────

    def _addons_verify(self, force=False, remote_checks=True):
        """Thin forwarder — the catalog fetch, scan and sha verification
        live in the AddonsController; this only kicks the panel chrome while
        the background verify runs."""
        if not self._addons.verify(force=force, remote_checks=remote_checks):
            return
        if self._addons.state.addons or self._addons.state.available:
            self._refresh_addons_footer()
        else:
            self._render_addons()

    def _addon_apply(self, recs):
        """Thin forwarder — the sequential install worker lives in the
        AddonsController; this kicks the "downloading" render and the busy
        button for it."""
        if not self._addons.apply(recs):
            return
        self._render_addons()
        self._set_btn_busy("Installing…")

    def _addon_update_all(self):
        self._addon_apply(self._addons.update_all())

    def _addon_remove(self, folder: str):
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Remove addon",
                f"Delete {folder} and all of its files?"):
            return
        self._addons.remove(folder)

    def _open_custom_addon_dialog(self):
        if self._settings_overlay is not None:
            return
        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, width=self._win_w, height=self._win_h)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov
        self.bind("<Escape>", lambda e: self._close_settings())

        # Same purple-dark theme as the Settings modal.
        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW, MH = 560, 230
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(x=(self._win_w - MW) // 2, y=(self._win_h - MH) // 2 - 20,
                    width=MW, height=MH)

        hdr = tk.Frame(panel, bg=P_HDR, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="ADD CUSTOM GIT ADDON",
                 font=self._font(13, "bold"),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=18)
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=16)
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=1).pack(fill="x")

        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=22, pady=(16, 12))
        tk.Label(body, text="REPOSITORY URL",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")
        url_var = tk.StringVar()
        tk.Entry(body, textvariable=url_var, bg=P_INP, fg=C_TEXT,
                 insertbackground=C_GOLD, relief="flat", font=self._mono(9),
                 highlightthickness=1, highlightbackground=P_BDR,
                 highlightcolor=C_GOLD).pack(fill="x", ipady=7, pady=(6, 6))
        tk.Label(body,
                 text="Allowed hosts: " + ", ".join(self._addons.git_hosts),
                 font=self._font(9), fg=C_TEXT_DIM, bg=P_BG).pack(anchor="w")
        err = tk.Label(body, text="", font=self._font(9),
                       fg=C_ERR, bg=P_BG)
        err.pack(anchor="w")

        def submit():
            url = url_var.get().strip().rstrip("/")
            if url.endswith(".git"):
                url = url[:-4]
            if not self._addons.is_allowed_git_url(url):
                err.configure(text="URL must be https from an allowed host.")
                return
            folder = url.rsplit("/", 1)[-1]
            if not folder or folder in (".", "..") or "\\" in folder:
                err.configure(text="Could not derive addon folder name.")
                return
            self._close_settings()
            self._log_line(f"\nInstalling custom addon {folder}…\n", "acct")
            self._addon_apply([{"folder": folder, "status": "available",
                                "git": url, "branch": None, "ref": None,
                                "toc": {}, "description": None,
                                "error": None}])

        btn = tk.Label(body, text="Install", font=self._font(11, "bold"),
                       fg=C_TEXT, bg=P_BDR, cursor="hand2", padx=16, pady=7)
        btn.pack(anchor="e", pady=(8, 0))
        btn.bind("<Button-1>", lambda e: submit())
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=P_BDR, fg=C_TEXT))

    # ── addons rendering ─────────────────────────────────────────────────────

    def _reset_addons_inner(self):
        """Replace the whole rows container with a fresh frame. A single
        destroy() tears the old subtree down inside Tk (C code) — far faster
        than destroying hundreds of row widgets one by one from Python."""
        cv  = self._addons_canvas
        old = getattr(self, "_addons_inner", None)
        if old is not None:
            old.destroy()
        inner = tk.Frame(cv, bg=C_PANEL)
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        if self._addons_win is None:
            self._addons_win = cv.create_window((0, 0), window=inner,
                                                anchor="nw",
                                                width=cv.winfo_width() or 1)
        else:
            cv.itemconfigure(self._addons_win, window=inner)
        cv.yview_moveto(0)
        self._addons_inner = inner

    def _on_addon_filter_changed(self, *_args):
        """Debounce search input — re-render once typing pauses, not on
        every keystroke."""
        if self._addon_filter_job is not None:
            self.after_cancel(self._addon_filter_job)
        self._addon_filter_job = self.after(250, self._apply_addon_filter)

    def _apply_addon_filter(self):
        self._addon_filter_job = None
        self._render_addons()

    def _render_addons(self):
        """Rebuild the addons list. Rows are created in small batches on the
        Tk event loop so a large catalog doesn't freeze the UI."""
        if not hasattr(self, "_addons_inner"):
            return
        self._addons_render_gen = getattr(self, "_addons_render_gen", 0) + 1
        gen = self._addons_render_gen
        self._reset_addons_inner()

        st  = self._addons.state.to_status_dict()
        # Snapshot the rendered content so AddonsLoaded can tell "nothing
        # changed" from a real update and skip the expensive rebuild.
        self._addons_rendered = (st["addons"], st["available"])
        flt = self._addon_filter_var.get().strip().lower()
        flt_compact = flt.replace(" ", "")

        def matches(rec):
            if not flt:
                return True
            title = strip_wow_colors((rec.get("toc") or {}).get("Title") or "")
            hay = f"{rec['folder']} {title}".lower()
            # Space-insensitive both ways: "sell value" finds SellValue,
            # "sellvalue" finds "Sell Value".
            return flt in hay or flt_compact in hay.replace(" ", "")

        def keep(lst):
            lst = sorted(lst, key=lambda r: r["folder"].lower())
            return [r for r in lst if matches(r)]

        installed = keep(st["addons"].values())
        # Recommended addons are no longer their own section — they're mixed
        # into Available and marked with a ★ badge. Sort recommended first so
        # they surface at the top of the list.
        available = keep(a for a in st["available"]
                         if a["folder"] not in st["addons"])
        available.sort(key=lambda a: (a["folder"] not in self._addons.recommended,
                                      a["folder"].lower()))

        work = []
        for title, rows in (("INSTALLED", installed),
                            ("AVAILABLE", available)):
            work.append(("header", title, rows))
            if self._addons.state.sections_open.get(title, True):
                work.extend(("row", rec) for rec in rows)
        self._addons_build_queue = work
        self._refresh_addons_footer()
        self._addons_build_step(gen)

    def _addons_build_step(self, gen: int):
        """Create up to one batch of queued headers/rows, then yield to the
        event loop; abandons the queue if a newer render has started."""
        if gen != self._addons_render_gen:
            return
        queue = self._addons_build_queue
        built = 0
        while queue and built < 14:
            item = queue.pop(0)
            if item[0] == "header":
                self._addon_section_header(item[1], item[2])
            else:
                self._addon_row(item[1])
            built += 1
        if queue:
            self.after(1, lambda: self._addons_build_step(gen))

    def _addon_section_header(self, title: str, rows: list):
        f = self._addons_inner
        is_open = self._addons.state.sections_open.get(title, True)

        hdr = tk.Frame(f, bg=C_PANEL)
        hdr.pack(fill="x", pady=(10, 2))
        arrow = tk.Label(hdr, text="▾" if is_open else "▸",
                         font=self._font(14, "bold"),
                         fg=C_GOLD, bg=C_PANEL, cursor="hand2", width=2)
        arrow.pack(side="left")
        lbl = tk.Label(hdr, text=title,
                       font=self._font(12, "bold"),
                       fg=C_GOLD, bg=C_PANEL, cursor="hand2")
        lbl.pack(side="left")
        tk.Label(hdr, text=f"  {len(rows)}", font=self._font(10),
                 fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")

        def toggle(_e=None, t=title):
            self._addons.state.sections_open[t] = \
                not self._addons.state.sections_open.get(t, True)
            self._render_addons()
        arrow.bind("<Button-1>", toggle)
        lbl.bind("<Button-1>", toggle)

        if is_open and not rows:
            msg = ("Verifying…" if self._addons.state.state == "verifying"
                   else "Nothing here.")
            tk.Label(f, text=msg, font=self._font(10), fg=C_TEXT_DIM,
                     bg=C_PANEL).pack(anchor="w", padx=8)

    def _addon_row(self, rec: dict):
        f = self._addons_inner
        installed = rec["folder"] in self._addons.state.addons
        toc = rec.get("toc") or {}

        warnings = []
        if toc.get("Interface") and toc["Interface"] != "11200":
            warnings.append(f"Made for client {toc['Interface']}")
        # pfUI bundles its own modules, so its .toc dependencies aren't real
        # missing addons — never warn about them.
        if installed and rec["folder"] != "pfUI":
            deps = [d.strip() for d in
                    (toc.get("Dependencies") or "").replace(";", ",").split(",")
                    if d.strip()]
            missing = [d for d in deps
                       if d not in self._addons.state.addons]
            if missing:
                warnings.append("Missing deps: " + ", ".join(missing))

        row = tk.Frame(f, bg=C_PANEL)
        row.pack(fill="x", pady=3)


        # right side first so it stays pinned to the edge
        if installed:
            # Trash can drawn as canvas shapes (handle, lid, tapered body
            # with slats) — same fixed-size approach as the download arrow.
            rm = tk.Canvas(row, width=20, height=18, bg=C_PANEL,
                           highlightthickness=0, cursor="hand2")
            rm.pack(side="right", padx=(8, 2))
            rm.create_rectangle(8, 2, 12, 4, fill="#8a4a4a", outline="",
                                tags="trash")
            rm.create_rectangle(4, 4, 16, 6, fill="#8a4a4a", outline="",
                                tags="trash")
            rm.create_polygon(5, 8, 15, 8, 14, 16, 6, 16,
                              fill="#8a4a4a", outline="", tags="trash")
            for x in (8, 10, 12):
                rm.create_line(x, 10, x, 14, fill=C_PANEL)
            rm.bind("<Button-1>",
                    lambda e, n=rec["folder"]: self._addon_remove(n))
            rm.bind("<Enter>", lambda e, c=rm:
                    c.itemconfigure("trash", fill=C_ERR))
            rm.bind("<Leave>", lambda e, c=rm:
                    c.itemconfigure("trash", fill="#8a4a4a"))
        else:
            # Download arrow drawn as a polygon — exact size and centering,
            # independent of any font, without inflating the row height.
            dl = tk.Canvas(row, width=20, height=18, bg=C_PANEL,
                           highlightthickness=0, cursor="hand2")
            dl.pack(side="right", padx=(8, 2))
            dl_item = dl.create_polygon(
                8, 3, 12, 3, 12, 9, 16, 9, 10, 15, 4, 9, 8, 9,
                fill=C_OK, outline="")
            dl.bind("<Button-1>",
                    lambda e, r=rec: self._addon_apply([dict(r)]))
            dl.bind("<Enter>", lambda e, c=dl, i=dl_item:
                    c.itemconfigure(i, fill="#8fdf8e"))
            dl.bind("<Leave>", lambda e, c=dl, i=dl_item:
                    c.itemconfigure(i, fill=C_OK))

        # Repo link on the right (like the Mods tab), between the status text
        # and the install/remove icon.
        if rec.get("git"):
            repo_url = rec["git"][:-4] if rec["git"].endswith(".git") \
                else rec["git"]
            lnk = tk.Label(row, text="⧉", font=self._font(10),
                           fg=C_TEXT_DIM, bg=C_PANEL, cursor="hand2")
            lnk.pack(side="right", padx=(4, 2))
            lnk.bind("<Button-1>", lambda e, u=repo_url: self._open_url(u))
            lnk.bind("<Enter>", lambda e, w=lnk: w.configure(fg=C_GOLD))
            lnk.bind("<Leave>", lambda e, w=lnk: w.configure(fg=C_TEXT_DIM))

        status = rec["status"]
        if status == "downloading":
            tk.Label(row, text="downloading…", font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="right", padx=4)
        elif status == "invalid" or rec.get("error"):
            # Short marker on the right; the full reason gets its own line
            # under the row (long messages would squeeze the description).
            tk.Label(row, text="⛔ Addon error", font=self._font(10),
                     fg=C_ERR, bg=C_PANEL).pack(side="right", padx=4)
        elif status == "outOfDate" and installed:
            upd = tk.Label(row, text="Update", font=self._font(10, "bold"),
                           fg=C_GOLD, bg=C_PANEL, cursor="hand2")
            upd.pack(side="right", padx=4)
            upd.bind("<Button-1>",
                     lambda e, r=rec: self._addon_apply([r]))
            upd.bind("<Enter>", lambda e, w=upd: w.configure(fg=C_GOLD_LT))
            upd.bind("<Leave>", lambda e, w=upd: w.configure(fg=C_GOLD))
        elif warnings:
            tk.Label(row, text=f"⚠ {warnings[0]}", font=self._font(10),
                     fg="#d4b43c", bg=C_PANEL).pack(side="right", padx=4)
        elif status == "upToDate":
            tk.Label(row, text="Up to date", font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="right", padx=4)
        elif status == "unknown":
            tk.Label(row, text="Not versioned", font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="right", padx=4)

        # name (WoW colour codes honoured) + repo link
        name_f = tk.Frame(row, bg=C_PANEL, width=250)
        name_f.pack(side="left", fill="y")
        name_f.pack_propagate(False)
        # Gold ★ badge for recommended addons; fixed-width slot keeps the
        # titles aligned whether or not the star is present.
        is_recommended = rec["folder"] in self._addons.recommended
        star = tk.Label(name_f, text="★" if is_recommended else "",
                        font=self._font(9), fg=C_GOLD, bg=C_PANEL,
                        width=2, anchor="w")
        star.pack(side="left")
        if is_recommended:
            self._add_tooltip(star, "Recommended addon")
        title = toc.get("Title") or rec["folder"]
        for seg, col in parse_wow_colored(title)[:6]:
            tk.Label(name_f, text=seg, font=self._font(10, "bold"),
                     fg=col or C_TEXT, bg=C_PANEL).pack(side="left")

        desc = strip_wow_colors(toc.get("Notes")
                                or rec.get("description") or "")
        tk.Label(row, text=desc, font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL, wraplength=self._row_wrap(), justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)

        if rec.get("error"):
            tk.Label(f, text=f"  ⚠  {rec['error']}",
                     font=self._font(9), fg=C_ERR, bg=C_PANEL,
                     wraplength=840, justify="left",
                     anchor="w").pack(fill="x", pady=(0, 3))

        tk.Frame(f, bg=C_DIVIDER, height=1).pack(fill="x", pady=(3, 0))

    def _refresh_addons_badge(self):
        if self._addons.updates_count != self._addon_updates_count:
            self._addon_updates_count = self._addons.updates_count
            self._draw_nav_tab("ADDONS")

    def _refresh_addons_footer(self):
        text, fg, cursor = self._addons.footer_state()
        self._addons_right_lbl.configure(text=text, fg=fg, cursor=cursor)

    # ── footer ────────────────────────────────────────────────────────────────

    def _build_footer(self):
        foot = tk.Frame(self, bg=C_BG, height=FOOT_H)
        foot.place(x=0, y=self._win_h - FOOT_H, width=self._win_w, height=FOOT_H)
        self._foot_frame = foot

        # Bottom-left column: status message on top, PLAY/UPDATE button in
        # the middle, client version at the bottom (with a bottom margin so
        # the content doesn't sit flush against the window edge).
        left = tk.Frame(foot, bg=C_BG)
        left.place(x=40, y=6)

        self._status_var = tk.StringVar(value="Ready to update")
        tk.Label(left, textvariable=self._status_var,
                 font=self._font(10, "bold"),
                 fg=C_TEXT, bg=C_BG).pack(anchor="w")

        # Thin halo frame around the button gives a soft glow that follows
        # the button state (gold for UPDATE, green for PLAY).
        self._btn_mode = "update"
        self._btn_glow = tk.Frame(left, bg="#4a3812")
        self._btn_glow.pack(anchor="w", pady=(6, 6))
        self._upd_btn = tk.Label(self._btn_glow, text="UPDATE",
                                 font=self._font(11, "bold"),
                                 fg="#ffffff", bg=C_GOLD,
                                 cursor="hand2",
                                 width=14, pady=7,
                                 anchor="center")
        self._upd_btn.pack(padx=3, pady=3)
        self._upd_btn.bind("<Button-1>", lambda e: self._btn_click())
        self._upd_btn.bind("<Enter>",    lambda e: self._btn_hover(True))
        self._upd_btn.bind("<Leave>",    lambda e: self._btn_hover(False))

        self._client_ver_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self._client_ver_var,
                 font=self._font(8), fg=C_TEXT_DIM, bg=C_BG).pack(
                 anchor="w", pady=(0, 36))

        pb_frame = tk.Frame(foot, bg=C_BG)
        pb_frame.place(x=250, y=0, width=progress_width(self._win_w), height=FOOT_H)
        self._pb_frame = pb_frame

        self._pb_canvas = tk.Canvas(pb_frame,
                                    height=6, bg=C_BG,
                                    highlightthickness=0)
        self._pb_canvas.pack(fill="x", side="bottom", padx=0,
                             ipady=0, pady=(0, 56))
        self._pb_width  = progress_width(self._win_w)
        self._pb_val    = 0.0

        self._prog_label_var = tk.StringVar(value="")
        tk.Label(pb_frame, textvariable=self._prog_label_var,
                 font=self._font(10), fg=C_TEXT, bg=C_BG).pack(
                 side="bottom", pady=(0, 6))

        self._draw_progress(0.0)

        tk.Label(foot, text=f"v{UPDATER_VERSION}",
                 font=self._mono(8),
                 fg="#555560", bg=C_BG).place(relx=1.0, rely=1.0,
                                              x=-10, y=-6, anchor="se")

        # Align the progress bar's bottom edge exactly with the PLAY/UPDATE
        # button's bottom edge once real geometry is known.
        def _align_pb():
            self.update_idletasks()
            gap = (foot.winfo_rooty() + FOOT_H) - (
                self._btn_glow.winfo_rooty() + self._btn_glow.winfo_height())
            if gap > 0:
                self._pb_canvas.pack_configure(pady=(0, gap))
        self.after(60, _align_pb)

    def _draw_progress(self, value: float):
        self._pb_val = max(0.0, min(1.0, value))
        c = self._pb_canvas
        w = self._pb_width
        c.delete("all")
        # Hide the bar entirely when idle (0) or finished/full (1) — it only
        # shows while something is actively downloading/updating.
        if self._pb_val <= 0.0 or self._pb_val >= 1.0:
            return
        c.create_rectangle(0, 0, w, 6, fill="#1e1e26", outline="")
        filled = int(w * self._pb_val)
        if filled > 0:
            for x in range(filled):
                t     = x / max(filled - 1, 1)
                r_val = int(0xc8 + t * (0xe8 - 0xc8))
                g_val = int(0x92 + t * (0xb8 - 0x92))
                b_val = int(0x2a + t * (0x4b - 0x2a))
                col   = f"#{r_val:02x}{g_val:02x}{b_val:02x}"
                c.create_line(x, 0, x, 6, fill=col)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _on_path_changed(self, *args):
        """Fires whenever the Game folder entry's value actually changes (typed,
        pasted, or set via Browse…). Delegates the folder-change reset (hash
        cache, config wipe, controller resets, re-verify) to the
        SettingsController and re-renders the panels it reset."""
        new_val = os.path.normpath(self._path_var.get().strip())
        last_val = os.path.normpath(self._last_path_val)

        if new_val == last_val:
            return

        changed = self._settings.set_path(new_val)
        self._last_path_val = new_val
        if not changed:
            return
        self._cfg = self._settings.state.config

        # Reset every session-level TTL/state so nothing from the previous
        # folder is served from memory: addons verify + its rendered list,
        # news feed timers, and the nav-tab update badges.
        self._mod_updates_count = self._mods.updates_count
        self._addon_updates_count = self._addons.updates_count
        self._draw_nav_tab("MODS")
        self._draw_nav_tab("ADDONS")
        self._render_addons()
        self._refresh_ready_state()

        # A deliberate folder change already covers the antivirus
        # recommendation — but on Windows still offer the exclusion once.
        if self._settings.should_prompt_av():
            self._prompt_av_exclusion()

    def _prompt_av_exclusion(self):
        """Ask whether to add the current game folder to Windows Defender
        exclusions (some mods can be mistakenly flagged by antivirus).
        Windows-only — a no-op elsewhere. The gating lives in the
        SettingsController; the dialog stays here."""
        if not self._settings.should_prompt_av():
            return
        from tkinter import messagebox
        if messagebox.askyesno(
                "Game folder changed",
                "It is highly recommended to add the game folder to your "
                "antivirus exclusions. Antivirus software may incorrectly "
                "detect some mods as threats and prevent them from being "
                "downloaded or installed properly.\n\n"
                "Do you want to add the game folder to Defender exclusions?",
                parent=self):
            self._settings.allow_through_antivirus()

    def _render_log(self, msg: str, tag: str = ""):
        """Normalize a raw log message (ensure a trailing newline, auto-tag
        when untagged) and append it to the log panel. Main thread only."""
        line = msg if msg.endswith("\n") else msg + "\n"
        if not tag:
            ml = line.lower()
            if "✓" in line or "success" in ml or "complete" in ml or "up to date" in ml:
                tag = "ok"
            elif "✗" in line or "error" in ml or "fail" in ml or "mismatch" in ml:
                tag = "err"
            elif line.strip().startswith("["):
                tag = "acct"
        self._log_line(line, tag)

    def _log_line(self, text: str, tag: str = ""):
        self._log_buffer.append((text, tag))
        txt = self._logwin_text
        if txt is not None:
            try:
                txt.configure(state="normal")
                if tag:
                    txt.insert("end", text, tag)
                else:
                    txt.insert("end", text)
                txt.see("end")
                txt.configure(state="disabled")
            except tk.TclError:
                self._logwin_text = None

    # ── settings ─────────────────────────────────────────────────────────────

    def _open_settings(self, event=None):
        if self._settings_overlay is not None:
            return

        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, width=self._win_w, height=self._win_h)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov

        self.bind("<Escape>", lambda e: self._close_settings())

        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW, MH = settings_rect(self._win_w, self._win_h)
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(x=(self._win_w - MW) // 2, y=(self._win_h - MH) // 2 - 20,
                    width=MW, height=MH)

        hdr = tk.Frame(panel, bg=P_HDR, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="SETTINGS", font=self._font(13, "bold"),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=18)
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=16)
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=1).pack(fill="x")

        PADX = 22
        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=PADX, pady=(16, 12))

        loc_row = tk.Frame(body, bg=P_BG)
        loc_row.pack(fill="x")
        tk.Label(loc_row, text="GAME FOLDER",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(side="left")
        opn = tk.Label(loc_row, text="Open folder", font=self._font(9),
                       fg=C_TEXT_DIM, bg=P_BG, cursor="hand2")
        opn.pack(side="left", padx=(16, 0))
        opn.bind("<Button-1>", lambda e: self._open_client_folder())
        opn.bind("<Enter>",    lambda e: opn.configure(fg=C_GOLD))
        opn.bind("<Leave>",    lambda e: opn.configure(fg=C_TEXT_DIM))

        # Same StringVar as the Update tab's Game folder entry — changing it
        # here fires the exact same folder-change mechanics immediately.
        path_row = tk.Frame(body, bg=P_BG)
        path_row.pack(fill="x", pady=(8, 0))
        ent = tk.Entry(path_row, textvariable=self._path_var,
                       bg=P_INP, fg=C_TEXT, relief="flat", font=self._mono(9),
                       state="readonly", readonlybackground=P_INP,
                       highlightthickness=1, highlightbackground=P_BDR,
                       highlightcolor=P_BDR)
        ent.pack(side="left", fill="x", expand=True, ipady=7)
        chg = tk.Label(path_row, text="Change",
                       font=self._font(10, "bold"),
                       fg=C_TEXT, bg=P_BDR, cursor="hand2", padx=16, pady=7)
        chg.pack(side="left", padx=(8, 0))
        chg.bind("<Button-1>", lambda e: self._settings_change_dir())
        chg.bind("<Enter>",    lambda e: chg.configure(bg=C_GOLD, fg="#000"))
        chg.bind("<Leave>",    lambda e: chg.configure(bg=P_BDR, fg=C_TEXT))

        tk.Label(body, text="DOWNLOAD MIRROR",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w", pady=(20, 4))
        mir = tk.Frame(body, bg=P_BG)
        mir.pack(fill="x")
        tk.Label(mir, text="●", font=self._font(9),
                 fg=C_OK, bg=P_BG).pack(side="left")
        tk.Label(mir, text=" Iceland", font=self._font(10, "bold"),
                 fg=C_TEXT, bg=P_BG).pack(side="left")
        self._mirror_status_lbl = tk.Label(mir, text="checking…",
                                           font=self._font(9),
                                           fg=C_TEXT_DIM, bg=P_BG)
        self._mirror_status_lbl.pack(side="left", padx=(8, 0))
        rf = tk.Label(mir, text="⟳", font=self._font(11),
                      fg=C_TEXT_DIM, bg=P_BG, cursor="hand2")
        rf.pack(side="left", padx=(6, 0))
        rf.bind("<Button-1>", lambda e: self._check_mirror_status())
        rf.bind("<Enter>",    lambda e: rf.configure(fg=C_GOLD))
        rf.bind("<Leave>",    lambda e: rf.configure(fg=C_TEXT_DIM))
        self._check_mirror_status()

        # Two equal-width columns via grid, so the right column keeps a fixed
        # position and reaches toward the right edge — regardless of how wide
        # the left column's text is.
        cols = tk.Frame(body, bg=P_BG)
        cols.pack(fill="x", pady=(22, 0))
        cols.columnconfigure(0, weight=3, uniform="s")
        cols.columnconfigure(1, weight=2, uniform="s")
        lcol = tk.Frame(cols, bg=P_BG)
        lcol.grid(row=0, column=0, sticky="nw")
        rcol = tk.Frame(cols, bg=P_BG)
        rcol.grid(row=0, column=1, sticky="nw")

        tk.Label(lcol, text="TROUBLESHOOTING",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")

        def _titem(icon, text, cmd, icon_color=C_GOLD):
            r = tk.Frame(lcol, bg=P_BG, cursor="hand2")
            r.pack(anchor="w", pady=(12, 0))
            # Monochrome glyphs in a fixed-width slot so all icons line up
            # and read at the same size (color emoji would render larger).
            ic = tk.Label(r, text=icon, font=self._font(11, family="Segoe UI Symbol"),
                          fg=icon_color, bg=P_BG, width=2, anchor="w")
            ic.pack(side="left")
            tl = tk.Label(r, text=text, font=self._font(10),
                          fg=C_TEXT, bg=P_BG)
            tl.pack(side="left")
            for w in (r, ic, tl):
                w.bind("<Button-1>", lambda e: cmd())
                w.bind("<Enter>", lambda e: tl.configure(fg=C_GOLD))
                w.bind("<Leave>", lambda e: tl.configure(fg=C_TEXT))

        _titem("✓", "Verify game files", self._settings_verify)
        _titem("☰", "Show logs", self._show_logs)
        if can_manage_antivirus():
            _titem("⛊", "Add game folder to Defender exclusions",
                   self._allow_through_antivirus)

        tk.Label(lcol, text="SUPPORT THE DEVELOPER",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w", pady=(22, 0))
        _titem("♥", "Ko-fi",
               lambda: self._open_url("https://ko-fi.com/rebased"),
               icon_color="#e8615f")
        _titem("☕", "Buy Me a Coffee",
               lambda: self._open_url("https://buymeacoffee.com/rebased"),
               icon_color="#b5854f")

        tk.Label(rcol, text="GENERAL",
                 font=self._font(10, "bold"),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")
        if can_launch_client():
            tk.Checkbutton(rcol, text=" Clear WDB on game launch",
                           variable=self._clear_wdb_var,
                           command=self._toggle_clear_wdb,
                           font=self._font(10), fg=C_TEXT, bg=P_BG,
                           activebackground=P_BG, activeforeground=C_TEXT,
                           selectcolor=P_INP, highlightthickness=0, bd=0,
                           cursor="hand2").pack(anchor="w", pady=(10, 0))
            tk.Checkbutton(rcol, text=" Close Octo Updater on game launch",
                           variable=self._close_on_launch_var,
                           command=self._toggle_close_on_launch,
                           font=self._font(10), fg=C_TEXT, bg=P_BG,
                           activebackground=P_BG, activeforeground=C_TEXT,
                           selectcolor=P_INP, highlightthickness=0, bd=0,
                           cursor="hand2").pack(anchor="w", pady=(10, 0))
        cb_auto_mods = tk.Checkbutton(
            rcol, text=" Install essential mods",
            variable=self._auto_mods_var, command=self._toggle_auto_mods,
            font=self._font(10), fg=C_TEXT, bg=P_BG,
            activebackground=P_BG, activeforeground=C_TEXT,
            selectcolor=P_INP, highlightthickness=0, bd=0, cursor="hand2")
        cb_auto_mods.pack(anchor="w", pady=(10, 0))
        self._add_tooltip(
            cb_auto_mods,
            "VanillaFixes will always be installed, even when this "
            "option is turned off")
        tk.Checkbutton(rcol, text=" Install recommended addons",
                       variable=self._auto_addons_var,
                       command=self._toggle_auto_addons,
                       font=self._font(10), fg=C_TEXT, bg=P_BG,
                       activebackground=P_BG, activeforeground=C_TEXT,
                       selectcolor=P_INP, highlightthickness=0, bd=0,
                       cursor="hand2").pack(anchor="w", pady=(10, 0))

    def _close_settings(self):
        self.unbind("<Escape>")
        if self._settings_overlay is not None:
            self._settings_overlay.destroy()
            self._settings_overlay = None
        # First run: the user has now committed to a game folder (kept the
        # default). Run the deferred verification against it and (over)write a
        # fresh Config.wtf with our defaults + realmList.
        if self._settings.state.first_run_verify_pending:
            self._settings.state.first_run_verify_pending = False
            self.after(100, lambda: self._start_verify(overwrite_config=True))
        # Apply any auto-install option the user turned on this session
        # (idempotent — a no-op when nothing is missing). The pending flags
        # live in the SettingsController.
        if self._settings.take_pending_auto_mods():
            self._install_missing_essential_mods()
        if self._settings.take_pending_auto_addons():
            self._install_missing_recommended_addons()
        # First run: the user accepted the auto-selected folder (never changed
        # it) and never added a Defender exclusion — recommend it now, once.
        if self._settings.state.first_run_av_pending:
            self._settings.av_prompt_dismissed()
            self._prompt_av_exclusion()

    def _open_client_folder(self):
        self._settings.open_client_folder()

    def _settings_change_dir(self):
        cur     = self._path_var.get()
        initial = cur if os.path.isdir(cur) else os.path.expanduser("~")
        chosen  = filedialog.askdirectory(
            title="Select game client folder",
            initialdir=initial, mustexist=False)
        if chosen:
            # normpath → backslashes; fires the folder-change reset
            self._path_var.set(os.path.normpath(chosen))

    def _settings_verify(self):
        self._close_settings()
        self._settings.verify_files()
        self._refresh_ready_state()

    def _allow_through_antivirus(self):
        """Thin forwarder — the Defender-exclusion logic and its log events
        live in the SettingsController."""
        self._settings.allow_through_antivirus()

    def _check_mirror_status(self):
        lbl = self._mirror_status_lbl
        lbl.configure(text="checking…", fg=C_TEXT_DIM)
        self._settings.check_mirror()

    def _on_mirror_status_changed(self, event):
        """Render a MirrorStatusChanged event into the Settings modal's
        download-mirror label."""
        if not isinstance(event, MirrorStatusChanged):
            return
        lbl = getattr(self, "_mirror_status_lbl", None)
        if lbl is None:
            return
        lbl.configure(text=event.text, fg=C_OK if event.ok else C_ERR)

    def _toggle_clear_wdb(self):
        val = self._clear_wdb_var.get()
        self._cfg = self._settings.set_clear_wdb(val)

    def _toggle_close_on_launch(self):
        val = self._close_on_launch_var.get()
        self._cfg = self._settings.set_close_on_launch(val)

    def _toggle_auto_mods(self):
        val = self._auto_mods_var.get()
        # The close-time pending install is armed by the controller.
        self._cfg = self._settings.set_auto_mods(val)

    def _toggle_auto_addons(self):
        val = self._auto_addons_var.get()
        # The close-time pending install is armed by the controller.
        self._cfg = self._settings.set_auto_addons(val)

    def _install_missing_essential_mods(self):
        """Thin forwarder — the missing-essential-mods logic (and the
        delegate to the ModsController) lives in the SettingsController."""
        if self._settings.install_missing_essential_mods():
            self._set_btn_busy("Installing…")
            self._status_var.set("Downloading mods…")

    def _install_missing_recommended_addons(self):
        """Thin forwarder — the missing-recommended-addons logic (and the
        delegate to the AddonsController) lives in the SettingsController."""
        if self._settings.install_missing_recommended_addons():
            self._render_addons()
            self._set_btn_busy("Installing…")

    def _show_logs(self):
        if self._logwin is not None:
            try:
                self._logwin.deiconify()
                self._logwin.lift()
                self._logwin.focus_force()
                return
            except tk.TclError:
                self._logwin = None
                self._logwin_text = None

        win = tk.Toplevel(self)
        win.title("Octo Updater — Logs")
        LW, LH = 760, 420
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{LW}x{LH}+{(sw - LW) // 2}+{(sh - LH) // 2}")
        win.configure(bg=C_BG)

        top = tk.Frame(win, bg=C_BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="SESSION LOG", font=self._font(9, "bold"),
                 fg=C_GOLD, bg=C_BG).pack(side="left")

        outer = tk.Frame(win, bg=C_BG)
        outer.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        sb = SlimScrollbar(outer, bg=C_BG)
        sb.pack(side="right", fill="y")
        txt = tk.Text(outer, bg=C_LOG_BG, fg=C_TEXT,
                      insertbackground=C_TEXT, relief="flat",
                      font=self._mono(9), wrap="word", state="disabled",
                      padx=10, pady=8, yscrollcommand=sb.set,
                      cursor="arrow", selectbackground=C_PANEL_BDR)
        txt.pack(side="left", fill="both", expand=True)
        sb.command = txt.yview
        for t, c in (("ok", C_OK), ("err", C_ERR),
                     ("dim", C_TEXT_DIM), ("acct", C_GOLD)):
            txt.tag_config(t, foreground=c)

        txt.configure(state="normal")
        for text, tag in self._log_buffer:
            if tag:
                txt.insert("end", text, tag)
            else:
                txt.insert("end", text)
        txt.see("end")
        txt.configure(state="disabled")

        def _close():
            self._logwin = None
            self._logwin_text = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _close)

        self._logwin = win
        self._logwin_text = txt

    # ── button helpers ───────────────────────────────────────────────────────────

    def _set_btn_play(self):
        self._btn_mode = "play"
        self._upd_btn.configure(text="PLAY", bg=C_GREEN_BTN, fg="#ffffff")
        self._btn_glow.configure(bg="#2b511d")

    def _set_btn_update(self):
        self._btn_mode = "update"
        self._upd_btn.configure(text="UPDATE", bg=C_GOLD, fg="#ffffff")
        self._btn_glow.configure(bg="#4a3812")

    def _set_btn_busy(self, label="…"):
        self._btn_mode = "busy"
        self._upd_btn.configure(text=label, bg="#2a2434", fg=C_TEXT_DIM)
        self._btn_glow.configure(bg="#211c2c")

    def _btn_hover(self, entering: bool):
        if self._btn_mode == "busy":
            return
        if entering:
            col  = C_GREEN_HOV if self._btn_mode == "play" else C_GOLD_LT
            glow = "#397024"   if self._btn_mode == "play" else "#5c4a16"
        else:
            col  = C_GREEN_BTN if self._btn_mode == "play" else C_GOLD
            glow = "#2b511d"   if self._btn_mode == "play" else "#4a3812"
        self._upd_btn.configure(bg=col)
        self._btn_glow.configure(bg=glow)

    def _refresh_ready_state(self):
        """Recompute the footer status/button after an operation finishes.
        PLAY is only offered when the client files are up to date AND no mod
        is in an error state — otherwise the button stays grey and inactive.
        The decision itself lives in UpdateController.compute_readiness."""
        r = self._updater.compute_readiness(
            addons_installing=self._addons.installing)
        if r.mode == "play":
            self._set_btn_play()
        elif r.mode == "update":
            self._set_btn_update()
        else:
            self._set_btn_busy(r.label)
        self._status_var.set(r.status)

    def _btn_click(self):
        if self._btn_mode == "play":
            self._launch_game()
        elif self._btn_mode == "update":
            self._start_update()

    def _launch_game(self):
        """Launch the game detached — the launch logic (VanillaFixes/WoW.exe
        choice, DXVK notice, clear-wdb, subprocess) lives in the
        UpdateController; this only drives the footer chrome and dialogs."""
        ok, dxvk_notice = self._updater.launch_game()
        if not ok:
            return
        if dxvk_notice:
            self._show_dxvk_notice()
        # Briefly disable PLAY so a double-click can't spawn two clients.
        self._set_btn_busy("PLAY")
        self._status_var.set("Launching...")
        # Optionally close the updater shortly after launch.
        if self._cfg.get("close_on_launch", False):
            self.after(1000, self._on_close)
            return
        self.after(5000, self._refresh_ready_state)

    def _show_dxvk_notice(self):
        """One-time DXVK first-launch notice (armed when dxvk was installed)."""
        from tkinter import messagebox
        messagebox.showinfo(
            "DXVK mod first launch",
            "Initial shader compilation may cause temporary in-game "
            "stuttering during the first launch. This is a normal process "
            "while the game builds its shader cache.\n\n"
            "Users with AMD GPUs experiencing stability issues can switch "
            "to DXVK 2.5.3",
            parent=self)

    # ── verify lifecycle ──────────────────────────────────────────────────────────

    def _start_verify(self, overwrite_config: bool = False):
        if not self._path_var.get().strip():
            self._set_btn_update()
            return
        self._updater.start_verify(overwrite_config)
        self._refresh_ready_state()

    # ── update lifecycle ──────────────────────────────────────────────────────────

    def _start_update(self):
        if self._updater.running:
            return
        if not self._path_var.get().strip():
            self._log_line("✗  Please set the game folder first.\n", "err")
            return
        self._updater.start_update()
        self._refresh_ready_state()

    # ── event handlers (Phase 1b controllers post; this class renders) ──────────

    def _on_status_changed(self, event):
        if isinstance(event, StatusChanged):
            self._status_var.set(event.text)

    def _on_progress_changed(self, event):
        if isinstance(event, ProgressChanged):
            self._draw_progress(event.value)
            self._prog_label_var.set(event.label)

    def _on_log_message(self, event):
        if isinstance(event, LogMessage):
            self._render_log(event.text, event.tag)

    def _on_operation_finished(self, event):
        if not isinstance(event, OperationFinished):
            return
        if event.kind == "mods":
            self._on_mods_finished(event)
            return
        if event.kind == "addons":
            self._on_addons_finished(event)
            return
        if event.ok:
            # The update worker reports the (post-patch) client version just
            # before finishing; surface it when a fresh one arrived.
            if self._updater.state.client_version:
                self._client_ver_var.set(self._updater.state.client_version)
            self._draw_progress(1.0)
            self._refresh_ready_state()
            # Game files are confirmed present now — install essential mods
            # if this is a fresh folder (first launch or folder just changed).
            self._maybe_install_essential_mods()
            # When mods were already initialized, the addons chain from the
            # mods operation never runs — trigger it directly.
            if self._settings.mods_initialized():
                self._maybe_install_default_addons()
        else:
            self._status_var.set("Update available!")
            self._draw_progress(0.0)
            self._set_btn_update()

    def _on_mods_finished(self, event):
        """Completion path for a mods apply (the old _do_inplace_update). The
        per-row widget refresh already happened on ModsLoaded; here the panel
        chrome and the fresh-setup chain are driven."""
        if not event.ok:
            self._status_var.set("Mods install failed — check the log")
            self._refresh_ready_state()
            return
        self._apply_btn.configure(text="Apply", bg=C_PANEL_BDR, fg=C_TEXT)
        self._refresh_apply_btn_visibility()
        self._refresh_mods_badge()
        # Fresh setup chain: once the default mods finished installing, the
        # recommended addons follow (no-op if already initialized).
        self._maybe_install_default_addons()

        # Any mod in an error state (download blocked, API limit, AV deleted
        # the archive, …) — bring the MODS tab up so the error is visible.
        # PLAY stays disabled via _refresh_ready_state.
        if self._mods.state.has_errors:
            self._switch_tab("MODS")
        self._refresh_ready_state()

    def _on_mods_loaded(self, event):
        if not isinstance(event, ModsLoaded):
            return
        self._render_mod_rows()
        self._refresh_mods_badge()
        self._refresh_ready_state()

    def _on_addons_loaded(self, event):
        if not isinstance(event, AddonsLoaded):
            return
        st = event.state.to_status_dict()
        if (self._addons_rendered is None
                or st["addons"] != self._addons_rendered[0]
                or st["available"] != self._addons_rendered[1]):
            self._render_addons()
        else:
            self._refresh_addons_footer()
        self._refresh_addons_badge()
        self._refresh_ready_state()

    def _on_addons_finished(self, event):
        """Completion of an addons install/update/remove worker — the READY
        state is recomputed so PLAY unlocks again (the readiness gate reads
        the controller's installing flag)."""
        self._refresh_ready_state()

    def _on_operation_failed(self, event):
        if isinstance(event, OperationFailed):
            self._status_var.set("Update failed — check the log")
            self._draw_progress(0.0)
            self._set_btn_update()

    def _on_news_loaded(self, event):
        if not isinstance(event, NewsLoaded):
            return
        if event.kind == "featured":
            self._render_featured(event.data.data, loading=event.data.loading,
                                  error=event.data.error)
        else:
            self._render_announcements(event.data.data,
                                       loading=event.data.loading,
                                       error=event.data.error)

    # ── queue polling ─────────────────────────────────────────────────────────

    def _poll(self):
        self._updater.poll()

        # Render the "Update available!" header label once the background
        # self-update check (UpdateController) reports a newer release.
        if (self._updater.updater_update_available
                and not self._update_available):
            self._update_available = True
            self._draw_update_label()

        # Drain the global app-log queue that helper functions write to.
        try:
            while True:
                msg, tag = _LOG_Q.get_nowait()
                self._render_log(msg, tag)
        except queue.Empty:
            pass

        self._events.dispatch_all()
        self.after(80, self._poll)


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point lives in octo_updater.py — this module only defines the GUI.
# ──────────────────────────────────────────────────────────────────────────────
# Agent guide: Qt UI conventions

Scope: `src/nostalgia_launcher/ui/qt/` — styling, dialogs, widgets.

## Theming modes

Two modes, decided once at startup by `palette_for_config(launcher.config())`
(never re-evaluated live):

- **Themed** — the launcher config carries a valid `theme` dict
  (`core/themes.has_valid_theme`): the global QSS (`theme_qss`) is applied
  through `apply_theme(widget, palette, extra_qss)`. Dialogs append their own
  background rule as `extra_qss`.
- **Native** — no theme, or an invalid one: `system_palette()` derives slots
  from the system `QPalette`; **no stylesheet is applied at all**
  (`apply_theme` sets `""`). Button variants and QSS-only styling vanish;
  per-widget inline styles built from palette attributes keep working, so
  content colors must stay readable on light *and* dark systems — semantic
  slots (`ok`, `err`, `pink`, `warn`, greens, parchment) deliberately do not
  follow the system palette.

Rules for both modes: never add palette slots for one-off needs (map to the
existing ones); the `purple` slot is the wordmark only; section titles use
`gold`, page/dialog titles use `gold_lt`. Test any visual change in all
three render checks: default themed, overridden theme, native.

## QSS f-string braces (breaks the suite)

The QSS in `ui/qt/main_window.py` is built from **f-strings that mix CSS
braces with `{p.*.name()}` interpolations**: an opening `{` must be `{{` and
a literal closing `}` must be `}}`. A single unescaped `}` is a hard
`SyntaxError` at import time that takes down every Qt test — it survives
`ruff format` too (which aborts on the unparseable file).

## Dialog close buttons

Qt settings dialogs are plain `QDialog`s (no frameless flag), so they
already get a native title-bar close button. Do NOT add a custom `✕` close
`QPushButton`/`QToolButton` — it renders a second close button beside the
native one. Close via the native title bar or `dialog.close()`; tests close
via `dialog.close()` (see `test_qt_settings_dialog.py` /
`test_qt_smoke.py`). The main `SettingsDialog` and `LinuxSettingsDialog`
follow this.

## Widget conventions

- Button language is all-caps for primary/global actions (`UPDATE`/`PLAY`,
  nav tabs) and Title Case for panel actions ("Apply", "Retry") — map
  controller machine strings to labels in the UI layer, never render raw
  `"retry"`/`"update"`.
- Recurring button looks come from QSS variants —
  `setProperty("variant", "primary"|"positive"|"outline"|"compact")` styled
  by `theme_qss` — not per-widget stylesheets.
- Dividers are `list_panel.make_hairline()`; section titles set
  `role="sectionTitle"`.
- Point sizes and paddings use the tokens in `ui/qt/metrics.py`
  (PT_*/PAD_*) — no ad hoc sizes. All palette colors (incl. pink/warn/
  btn_text) are themable slots in `core/themes.py`; never hardcode hex in
  widgets.
- Icon-only controls get a tooltip + `setAccessibleName`.
- The LinuxSettingsDialog uses the `linuxSettings*` objectName prefix (tests
  assert it). Footer pseudo-actions are real `QToolButton`s, not clickable
  labels.

## Session-log window

`LogWindow` is a **top-level** widget (no parent): it gets its own taskbar
entry and outlives main-window stacking. `MainWindow` owns its lifecycle —
creation is lazy, `WA_DeleteOnClose` destroys it on close, and `destroyed`
resets `_logWindow`. The Settings "Show logs" row (`settingsLogs`) is a
*toggle*: `logsToggleRequested` asks MainWindow to open or close it, and
MainWindow pushes visibility back via `SettingsDialog.set_logs_open`, which
flips the row label between "Show logs"/"Hide logs". Keep that one-way data
flow (dialog emits intent, window owns state) when touching either side.

# Qt display/scaling manual QA matrix

The Qt UI is layout-based and renders in *logical* pixels — Qt applies the
display scale factor (device-pixel ratio) itself at compositor time, and the
window's minimum size (~560x420) is expressed in logical units so it holds at
any scaling. The automated QML suite (`tests/test_qml_*.py`) runs offscreen
on every host and covers loading, bindings and interactions. What it cannot
judge is *how it looks* — this matrix records that human pass.

## How to run

On a real desktop session, first run the automated QML checks:

```sh
uv run pytest tests/test_qml_shell.py tests/test_qml_settings.py tests/test_qml_custom.py -q
```

Then launch the app itself at each display scale you test:

```sh
uv run python -m nostalgia_launcher
```

and tick the rows below. Fill the scale column with the actual OS setting;
the device-pixel ratio Qt printed should match it.

## Matrix

Scale = OS display scaling. ✅ = good, ⚠️ = needs a look, ✗ = broken, — = n/a.

| Platform | Scale | Window opens sized reasonably | Fonts legible | Tabs/panels render | Settings dialog fits | Progress/footer visible | No clipping at min size | Resize reflows |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Windows 11 | 100% | | | | | | | |
| Windows 11 | 125% | | | | | | | |
| Windows 11 | 150% | | | | | | | |
| Windows 11 | 200% | | | | | | | |
| GNOME Wayland | 100% | | | | | | | |
| GNOME Wayland | 125% (fractional) | | | | | | | |
| GNOME Wayland | 150% | | | | | | | |
| GNOME Wayland | 200% | | | | | | | |
| GNOME X11 | 100% | | | | | | | |
| GNOME X11 | 125% | | | | | | | |
| GNOME X11 | 150% | | | | | | | |
| GNOME X11 | 200% | | | | | | | |
| KDE Plasma Wayland | 100% | | | | | | | |
| KDE Plasma Wayland | 125% | | | | | | | |
| KDE Plasma Wayland | 150% | | | | | | | |
| KDE Plasma Wayland | 200% | | | | | | | |
| KDE Plasma X11 | 100% | | | | | | | |
| KDE Plasma X11 | 125% | | | | | | | |
| KDE Plasma X11 | 150% | | | | | | | |
| KDE Plasma X11 | 200% | | | | | | | |
| macOS Retina | 1x | | | | | | | |
| macOS Retina | 2x | | | | | | | |

### Notes for each column

- **Window opens sized reasonably** — roughly the 1000x700 design, capped at
  ~90% of the screen; never overflowing the work area on small displays.
- **Fonts legible** — headers, nav tabs, footer text and panel body are
  readable (no text clipping from fixed `pt` sizes at high scale).
- **Tabs/panels render** — all four tabs (News, Tweaks, Addons, Mods) switch
  cleanly and their panels paint without artifacts or overlap.
- **Settings dialog fits** — opens on-screen, min 520x440, no clipped rows,
  close button usable.
- **Progress/footer visible** — status label, UPDATE/PLAY button, version and
  progress bar all show in the footer without being pushed off-window.
- **No clipping at min size** — shrink to the minimum (~560x420); header,
  footer and the current panel stay fully usable (scrollbars where expected).
- **Resize reflows** — resizing the window reflows content (news splitter,
  rows, footer) instead of clipping or leaving dead space.

### Known-good reference (Dev box)

| Platform | Scale | DPR reported by Qt | Notes |
| --- | --- | --- | --- |
| X11 (this host) | 200% | 2.0 | manual pass (no automated real-display test); window, tabs, settings all fine |

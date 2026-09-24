# Agent guide: QML UI conventions

Scope: `src/nostalgia_launcher/ui/qml/` (views in `qml/`, view-models
beside them) plus the toolkit glue in `ui/` (`bridge.py`, `theme.py`,
`relaunch.py`, `app_lock_qt.py`).

## Layering (hard rule)

- `controllers/` stay toolkit-agnostic: they post dataclass events to
  `EventDispatcher` and never touch UI code. QML never drives workers
  directly — view-models (`QObject`s with `Property` + `notify`) subscribe
  to `ControllerBridge` signals (or take plain `on_event` dataclasses).
- Controller calls from UI go through injected callbacks
  (`set_*_handler` / `set_handlers`), never imports — keeps models
  unit-testable without controllers.
- Formatting (sizes, speeds, ages) happens in Python (`core/helpers`),
  never in QML expressions.

## Theming

- QtQuick.Controls **Material**, Dark + brand-gold accent, pinned in
  `create_qml_app()` (`QQuickStyle.setStyle("Material")`); `main.qml`
  sets `Material.theme: Dark`, `Material.accent: appTheme.colors["C_GOLD"]`.
- Colors come from the `appTheme` context property (`ThemeBridge` over
  `ui/theme.py:Palette`); never hardcode hex in QML. Never add palette
  slots for one-off needs. Section titles use gold, page titles gold_lt.
- Test any visual change in all three render checks: default themed,
  overridden theme, native (system-derived palette).

## QML conventions

- Button language is all-caps for primary/global actions (`UPDATE`/`PLAY`,
  nav tabs) and Title Case for panel actions ("Apply", "Retry") — machine
  strings (`"retry"`/`"update"`) stay in models, labels in QML.
- Every interactive element that carries an `objectName` keeps it stable:
  engine tests find nodes via `findChild(QObject, "…")`.
- Icon-only controls get `ToolTip.text` + `Accessible.name`.
- Context properties are engine-global: `launcherState`, `appTheme`,
  `newsModel`, `updateState`, `modsModel`, `assetsModel`, `addonsModel`,
  `settingsModel`, `logModel`, `wizard`, `customModModel`,
  `customAddonModel`, `customAssetModel`, `linuxModel`. Do NOT name a
  context property `theme` — it collides with a QtQuick.Controls internal
  and binds null (use `appTheme`).
- Destructive actions confirm via `MessageDialog`; checkbox echo
  (`onCheckedChanged` re-firing on programmatic refresh) is dropped
  model-side by comparing against current state.

## Tests

- QML tests set `QT_QPA_PLATFORM=offscreen` + `QT_QUICK_BACKEND=software`
  before importing PySide6, pin `QQuickStyle.setStyle("Material")`, load
  `main.qml` (or `WizardWindow.qml`) with stub models, and assert view
  state follows the models. Model unit tests need no engine.
- Engine stderr `TypeError: Cannot read property … of null` lines at load
  are a known Qt/offscreen artifact — bindings resolve correctly; tests
  assert post-load values.

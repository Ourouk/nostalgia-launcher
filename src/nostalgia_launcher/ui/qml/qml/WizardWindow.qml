// First-launch import window (Phase 6b). Hosts the WizardView dialog
// modally before any main window exists; `cli._first_launch` drives it
// through `ui/qml/wizard.py:run_import_wizard_qml` (nested event loop,
// accepted/rejected ends the run with the selection or None).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "qmlWizardWindow"
    visible: true
    width: 640
    height: 600
    title: "Welcome to Nostalgia Launcher"
    color: appTheme.colors["C_BG"]
    Material.theme: Material.Dark
    Material.accent: appTheme.colors["C_GOLD"]

    signal wizardDone(bool accepted)

    WizardView {
        id: wizardDialog
        objectName: "qmlWizardDialog"
        onAccepted: root.wizardDone(true)
        onRejected: root.wizardDone(false)
    }

    Component.onCompleted: wizardDialog.open()
}

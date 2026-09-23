// Nostalgia Launcher QML shell.
//
// Mirrors ui/qt/main_window.py: header wordmark, NEWS/UPDATE/ADDONS/MODS/
// ASSETS tabs, footer status + progress, gear-button settings dialog and
// session-log viewer. Bindings read the live view-models (`launcherState`,
// `newsModel`, `updateState`, `modsModel`, `assetsModel`, `addonsModel`,
// `settingsModel`, `logModel`, `appTheme`) exposed by ui/qml/app.py.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "qmlMainWindow"
    visible: false
    width: 1000
    height: 700
    title: "Nostalgia Launcher"
    color: appTheme.colors["C_BG"]
    Material.theme: Material.Dark
    Material.accent: appTheme.colors["C_GOLD"]

    header: ToolBar {
        objectName: "qmlHeader"
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Label {
                objectName: "qmlWordmark"
                text: "NOSTALGIA LAUNCHER"
                font.bold: true
                font.pointSize: 13
                color: appTheme.colors["C_GOLD_LT"]
            }
            Item { Layout.fillWidth: true }
            Label {
                objectName: "qmlVersionPill"
                text: "QML preview"
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
            }
            ToolButton {
                objectName: "qmlGearButton"
                text: "⚙"
                Accessible.name: "Open settings"
                ToolTip.text: "Settings"
                ToolTip.visible: hovered
                onClicked: settingsDialog.open()
            }
        }
    }

    SettingsView {
        id: settingsDialog
        objectName: "qmlSettingsDialog"
    }

    LogDialog {
        id: logDialog
        objectName: "qmlLogDialog"
        onLogDialogVisible: (open) => {
            if (open)
                logModel.refresh();
            settingsModel.setLogsOpen(open);
        }
    }

    Connections {
        target: settingsModel
        function onLogsRequested() {
            if (logDialog.visible)
                logDialog.close();
            else
                logDialog.open();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: navBar
            objectName: "qmlNavBar"
            Layout.fillWidth: true
            TabButton { text: "NEWS" }
            TabButton { text: "UPDATE" }
            TabButton {
                text: addonsModel.updatesCount > 0 ? "ADDONS (" + addonsModel.updatesCount + ")" : "ADDONS"
            }
            TabButton {
                text: modsModel.updatesCount > 0 ? "MODS (" + modsModel.updatesCount + ")" : "MODS"
            }
            TabButton {
                text: assetsModel.updatesCount > 0 ? "ASSETS (" + assetsModel.updatesCount + ")" : "ASSETS"
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: navBar.currentIndex

            NewsView {
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            UpdateView {
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            AddonsView {
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
            ContentView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentModel: modsModel
                tabTitle: "MODS"
                legendText: "★ required"
                essentialText: "★  Install Required"
            }
            ContentView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentModel: assetsModel
                tabTitle: "ASSETS"
                legendText: ""
                essentialText: "★  Install Essential"
            }
        }
    }

    footer: ToolBar {
        objectName: "qmlFooter"
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Label {
                objectName: "qmlStatusLabel"
                Layout.fillWidth: true
                text: launcherState.statusText
                elide: Text.ElideRight
                font.pointSize: 10
                color: appTheme.colors["C_TEXT"]
            }
            ProgressBar {
                objectName: "qmlProgressBar"
                Layout.preferredWidth: 220
                from: 0
                to: 100
                value: launcherState.progressValue * 100
            }
            Button {
                objectName: "qmlPrimaryButton"
                text: updateState.primaryLabel
                enabled: updateState.primaryEnabled
                onClicked: updateState.primary()
            }
        }
    }
}

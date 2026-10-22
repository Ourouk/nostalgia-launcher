// Nostalgia Launcher QML shell.
//
// Implements the main window: header wordmark, NEWS/UPDATE/ADDONS/MODS/
// ASSETS tabs, footer status + progress, gear-button settings dialog and
// session-log viewer. Bindings read the live view-models (`launcherState`,
// `newsModel`, `updateState`, `modsModel`, `assetsModel`, `addonsModel`,
// `settingsModel`, `logModel`, `appTheme`) exposed by ui/qml/app.py.
// Settings owns its sub-dialogs (Linux/UMU, profile-import wizard); the
// log is an independent window.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    objectName: "qmlMainWindow"
    visible: false
    // ponytail: mirrors metrics.initial_window_size (92% cap, 560x420 floor).
    width: Math.min(1000, Screen.desktopAvailableWidth * 0.92)
    height: Math.min(700, Screen.desktopAvailableHeight * 0.92)
    minimumWidth: 560
    minimumHeight: 420
    title: "Nostalgia Launcher"
    color: appTheme.colors["C_BG"]
    Material.theme: appTheme.themed ? Material.Dark : Material.System
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
                visible: launcherState.logoSource === ""
                text: launcherState.serverName !== "" ? launcherState.serverName : "NOSTALGIA LAUNCHER"
                font.bold: true
                font.pointSize: 13
                color: appTheme.colors["C_GOLD_LT"]
            }
            Image {
                objectName: "qmlLogo"
                visible: launcherState.logoSource !== ""
                source: launcherState.logoSource
                // Widget-shell parity: fit within 28px tall, 320px wide.
                // Height drives the scale (aspect kept); maximumWidth
                // caps very wide logos. No sourceSize.width: upscaling a
                // narrow logo would stretch it to the cap.
                sourceSize.height: 28
                fillMode: Image.PreserveAspectFit
                Layout.preferredHeight: 28
                Layout.maximumWidth: 320
                Accessible.name: "Server logo"
            }
            Item { Layout.fillWidth: true }
            ComboBox {
                id: profileCombo
                objectName: "qmlProfileCombo"
                Accessible.name: "Active profile"
                ToolTip.text: "Active profile — selecting another one restarts the launcher"
                ToolTip.visible: hovered
                model: settingsModel.profiles
                currentIndex: Math.max(0, settingsModel.profiles.indexOf(settingsModel.activeProfile))
                onActivated: (index) => settingsModel.requestSwitch(textAt(index))
                Layout.preferredWidth: 320
                Layout.maximumWidth: 420
                Layout.preferredHeight: 28
                Layout.topMargin: 6
                Layout.bottomMargin: 6
                // Elide long server names so the dropdown arrow stays
                // visible (qmllint can't resolve profileCombo.displayText
                // statically, but it is valid at runtime).
                contentItem: Text {
                    text: profileCombo.displayText
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 8
                    rightPadding: 28
                    color: appTheme.colors["C_TEXT"]
                }
            }
            Label {
                objectName: "qmlVersionPill"
                visible: settingsModel.clientVersion !== ""
                text: settingsModel.clientVersion
                font.pointSize: 11
                font.bold: true
                color: appTheme.colors["C_TEXT"]
            }
            ToolButton {
                objectName: "qmlGearButton"
                text: "⚙"
                font.pointSize: 13
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

    MessageDialog {
        id: switchConfirm
        title: "Switch profile"
        text: "Switch to profile '" + settingsModel.switchPrompt + "'? (The launcher will restart.)"
        buttons: MessageDialog.Yes | MessageDialog.No
        visible: settingsModel.switchPrompt !== ""
        onAccepted: settingsModel.resolveSwitch(true)
        onRejected: {
            settingsModel.resolveSwitch(false)
            profileCombo.currentIndex = Math.max(0, settingsModel.profiles.indexOf(settingsModel.activeProfile))
        }
    }

    MessageDialog {
        id: realmConfirm
        title: "Realm mismatch"
        text: updateState.realmPrompt + "\n\nUpdate the realm before launching?"
        buttons: MessageDialog.Yes | MessageDialog.No
        visible: updateState.realmPrompt !== ""
        onAccepted: updateState.resolveRealm(true)
        onRejected: updateState.resolveRealm(false)
    }

    CustomModDialog {
        id: customModDialog
        objectName: "qmlCustomModDialog"
    }

    CustomAddonDialog {
        id: customAddonDialog
        objectName: "qmlCustomAddonDialog"
    }

    CustomAssetDialog {
        id: customAssetDialog
        objectName: "qmlCustomAssetDialog"
    }

    BrowseView {
        id: browseModsDialog
        objectName: "qmlBrowseModsDialog"
        title: "BROWSE MODS"
        browseModel: browseModsModel
        formSource: "CustomModForm.qml"
    }

    BrowseView {
        id: browseAssetsDialog
        objectName: "qmlBrowseAssetsDialog"
        title: "BROWSE ASSETS"
        browseModel: browseAssetsModel
        formSource: "CustomAssetForm.qml"
    }

    BrowseView {
        id: browseAddonsDialog
        objectName: "qmlBrowseAddonsDialog"
        title: "BROWSE ADDONS"
        browseModel: browseAddonsModel
        formSource: "CustomAddonForm.qml"
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

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: navBar
            objectName: "qmlNavBar"
            Layout.fillWidth: true
            TabButton { text: "NEWS" }
            TabButton {
                objectName: "qmlUpdateTab"
                text: "UPDATE"
                enabled: settingsModel.clientUpdates && updateState.updateTabEnabled
                onEnabledChanged: {
                    if (!enabled && navBar.currentIndex === 1)
                        navBar.currentIndex = 0
                }
            }
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
                legendText: "★ required · ☐ off = disabled · ● = unapplied (Apply to confirm)"
                essentialText: "★  Install Required"
                loadingText: "Loading mods…"
            }
            ContentView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentModel: assetsModel
                tabTitle: "ASSETS"
                legendText: "☐ off = disabled · ● = unapplied (Apply to confirm)"
                essentialText: "★  Install Essential"
                loadingText: "Loading assets…"
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
                visible: launcherState.progressVisible
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

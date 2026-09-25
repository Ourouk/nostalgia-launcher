// Settings dialog (Phase 6a): Game /
// Sources / Profiles / Troubleshooting tabs. Reads the `settingsModel`
// context property (ui/qml/settings.py:SettingsModel). Profile import and
// the Linux (UMU) window land with the wizard dialogs (Phase 6b).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "qmlSettingsDialog"
    title: "Settings"
    visible: false
    width: 680
    height: 700
    minimumWidth: 560
    minimumHeight: 480
    color: appTheme.colors["C_BG"]
    Material.theme: Material.Dark
    Material.accent: appTheme.colors["C_GOLD"]

    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        Label {
            anchors.fill: parent
            anchors.leftMargin: 18
            verticalAlignment: Text.AlignVCenter
            text: "SETTINGS"
            font.bold: true
            font.pointSize: 13
            color: appTheme.colors["C_GOLD_LT"]
        }
    }

    // Single-instance raise: the gear button calls showSettings(),
    // which shows a hidden window or raises the visible one.
    function showSettings() {
        if (!visible)
            show();
        requestActivate();
        raise();
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: settingsTabs
            objectName: "qmlSettingsTabs"
            Layout.fillWidth: true
            TabButton { text: "Game" }
            TabButton { text: "Sources" }
            TabButton { text: "Profiles" }
            TabButton { text: "Troubleshooting" }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: settingsTabs.currentIndex

            // ── Game ────────────────────────────────────────────
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    anchors.topMargin: 12
                    anchors.bottomMargin: 12
                    spacing: 8
                    Label {
                        text: "GAME FOLDER"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    TextField {
                        objectName: "qmlSettingsPath"
                        Layout.fillWidth: true
                        text: settingsModel.gamePath
                        readOnly: true
                        selectByMouse: true
                        placeholderText: text === "" ? (settingsModel.gameSuggestion !== "" ? settingsModel.gameSuggestion : "Select the game folder containing WoW.exe") : ""
                        placeholderTextColor: appTheme.colors["C_TEXT_DIM"]
                        font.family: "monospace"
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            objectName: "qmlSettingsChange"
                            text: "Change"
                            onClicked: folderPicker.open()
                        }
                        Button {
                            text: "Open folder"
                            flat: true
                            onClicked: settingsModel.openClientFolder()
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Label {
                        text: "GENERAL"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    CheckBox {
                        objectName: "qmlSettingsClearWdb"
                        text: "Clear WDB on game launch"
                        visible: settingsModel.canLaunch
                        checked: settingsModel.clearWdb
                        onToggled: settingsModel.setClearWdb(checked)
                    }
                    CheckBox {
                        objectName: "qmlSettingsCloseOnLaunch"
                        text: "Close Nostalgia Launcher on game launch"
                        visible: settingsModel.canLaunch
                        checked: settingsModel.closeOnLaunch
                        onToggled: settingsModel.setCloseOnLaunch(checked)
                    }
                    CheckBox {
                        objectName: "qmlSettingsClientUpdate"
                        text: "Enable client updates"
                        checked: settingsModel.clientUpdates
                        onToggled: settingsModel.setClientUpdates(checked)
                    }
                    Button {
                        objectName: "qmlSettingsLinuxButton"
                        text: "Linux (UMU) Settings…"
                        visible: settingsModel.isLinux
                        flat: true
                        onClicked: settingsModel.requestLinux()
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ── Sources ─────────────────────────────────────────
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    anchors.topMargin: 12
                    anchors.bottomMargin: 12
                    spacing: 8
                    Label {
                        text: "DOWNLOAD SOURCE"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    Repeater {
                        model: settingsModel.sources
                        RowLayout {
                            width: parent.width
                            Label {
                                text: "●"
                                color: modelData.status === "online" ? appTheme.colors["C_OK"] : (modelData.status === "offline" ? appTheme.colors["C_ERR"] : appTheme.colors["C_TEXT_DIM"])
                            }
                            Label {
                                text: modelData.name
                                font.bold: true
                                font.pointSize: 10
                                color: appTheme.colors["C_TEXT"]
                            }
                            Label {
                                text: modelData.status
                                font.pointSize: 9
                                color: appTheme.colors["C_TEXT_DIM"]
                            }
                        }
                    }
                    Button {
                        objectName: "qmlSettingsSourceRefresh"
                        text: "⟳  Check source"
                        flat: true
                        onClicked: settingsModel.checkSources()
                    }
                    Label {
                        text: "CATALOG REGISTRIES"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    CheckBox {
                        text: "Use community default addons catalog"
                        enabled: settingsModel.addonsDefaultAvail
                        checked: settingsModel.addonsDefault && settingsModel.addonsDefaultAvail
                        onToggled: settingsModel.setAddonsDefault(checked)
                    }
                    CheckBox {
                        text: "Use community default mods catalog"
                        enabled: settingsModel.modsDefaultAvail
                        checked: settingsModel.modsDefault && settingsModel.modsDefaultAvail
                        onToggled: settingsModel.setModsDefault(checked)
                    }
                    RegistryRow {
                        label: "ADDONS"
                        url: settingsModel.addonsUrl
                        onApply: (text) => settingsModel.applyAddonsUrl(text)
                        onReset: () => settingsModel.resetAddonsUrl()
                        onReload: () => settingsModel.reloadAddons()
                        onOpenCustom: () => settingsModel.openAddonsCustom()
                        onClearCustom: () => settingsModel.clearAddonsCustom()
                    }
                    RegistryRow {
                        label: "MODS"
                        url: settingsModel.modsUrl
                        onApply: (text) => settingsModel.applyModsUrl(text)
                        onReset: () => settingsModel.resetModsUrl()
                        onReload: () => settingsModel.reloadMods()
                        onOpenCustom: () => settingsModel.openModsCustom()
                        onClearCustom: () => settingsModel.clearModsCustom()
                    }
                    Label {
                        objectName: "qmlSettingsRegistryStatus"
                        Layout.fillWidth: true
                        text: settingsModel.registryStatus
                        font.pointSize: 9
                        color: appTheme.colors["C_ERR"]
                        wrapMode: Text.WordWrap
                        visible: settingsModel.registryStatus !== ""
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ── Profiles ────────────────────────────────────────
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    anchors.topMargin: 12
                    anchors.bottomMargin: 12
                    spacing: 8
                    Label {
                        text: "PROFILES"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "One profile per server — importing a configuration creates its profile, named after the server. Switch profiles from the selector in the main-window header; switching restarts the launcher."
                        font.pointSize: 9
                        color: appTheme.colors["C_TEXT_DIM"]
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ComboBox {
                            id: profilesCombo
                            objectName: "qmlProfilesCombo"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 120
                            model: settingsModel.profiles
                            currentIndex: Math.max(0, settingsModel.profiles.indexOf(settingsModel.activeProfile))
                            // Elide long server names so Import/Delete stay
                            // visible (same pattern as the header combo).
                            contentItem: Text {
                                text: profilesCombo.displayText
                                elide: Text.ElideRight
                                verticalAlignment: Text.AlignVCenter
                                color: appTheme.colors["C_TEXT"]
                            }
                        }
                        Button {
                            objectName: "qmlProfilesImport"
                            text: "Import…"
                            onClicked: {
                                wizard.reset();
                                importWizard.open();
                            }
                        }
                        Button {
                            objectName: "qmlProfilesDelete"
                            text: "Delete"
                            onClicked: deleteConfirm.open()
                        }
                    }
                    Label {
                        objectName: "qmlProfilesStatus"
                        Layout.fillWidth: true
                        text: settingsModel.profilesStatus
                        font.pointSize: 9
                        color: appTheme.colors["C_ERR"]
                        wrapMode: Text.WordWrap
                        visible: settingsModel.profilesStatus !== ""
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ── Troubleshooting ─────────────────────────────────
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: 16
                    anchors.rightMargin: 16
                    anchors.topMargin: 12
                    anchors.bottomMargin: 12
                    spacing: 8
                    Label {
                        text: "TROUBLESHOOTING"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    Button {
                        objectName: "qmlSettingsVerify"
                        text: "✓  Verify game files"
                        flat: true
                        onClicked: settingsModel.verifyFiles()
                    }
                    Button {
                        objectName: "qmlSettingsLogs"
                        text: settingsModel.logsOpen ? "Hide logs" : "Show logs"
                        flat: true
                        onClicked: settingsModel.requestLogs()
                    }
                    Button {
                        objectName: "qmlSettingsAv"
                        text: "⛊  Add game folder to Defender exclusions"
                        visible: settingsModel.canAntivirus
                        flat: true
                        onClicked: settingsModel.allowAntivirus()
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }

    footer: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Item { Layout.fillWidth: true }
            Button {
                objectName: "qmlSettingsClose"
                text: "Close"
                onClicked: root.close()
            }
        }
    }

    FileDialog {
        id: folderPicker
        objectName: "qmlSettingsFolderPicker"
        title: "Select game client folder"
        // NOTE: FileDialog.OpenDirectory is undefined on some backends
        // (offscreen probe: OpenFile=0, OpenDirectory=undefined) — the
        // folder mode is the dialog default, so only set fileMode when
        // the enum exists.
        Component.onCompleted: {
            if (FileDialog.OpenDirectory !== undefined)
                fileMode = FileDialog.OpenDirectory
        }
        onAccepted: settingsModel.setGameFolder(currentFolder.toString().replace(/^file:\/\//, ""))
    }

    MessageDialog {
        id: deleteConfirm
        title: "Delete profile"
        text: "Delete this profile? Its server config, state and caches will be removed."
        buttons: MessageDialog.Yes | MessageDialog.No
        onAccepted: settingsModel.deleteProfile(profilesCombo.currentText)
    }

    // One shared registry row (ADDONS / MODS).
    component RegistryRow: ColumnLayout {
        property string label: ""
        property string url: ""
        signal apply(string text)
        signal reset()
        signal reload()
        signal openCustom()
        signal clearCustom()
        Layout.fillWidth: true
        spacing: 2
        Label {
            text: label
            font.bold: true
            font.pointSize: 9
            color: appTheme.colors["C_TEXT"]
        }
        TextField {
            id: urlField
            Layout.fillWidth: true
            text: url
            selectByMouse: true
            font.family: "monospace"
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Button {
                text: "Apply"
                onClicked: apply(urlField.text)
            }
            Button {
                text: "Reset"
                flat: true
                ToolTip.text: "Use the default server catalog"
                ToolTip.visible: hovered
                onClicked: reset()
            }
            Button {
                text: "Reload"
                flat: true
                ToolTip.text: "Fetch the catalog now and refresh the tab"
                ToolTip.visible: hovered
                onClicked: reload()
            }
            Button {
                text: "Open custom file"
                flat: true
                onClicked: openCustom()
            }
            Button {
                text: "Clear custom entries"
                flat: true
                onClicked: clearCustom()
            }
        }
    }
}

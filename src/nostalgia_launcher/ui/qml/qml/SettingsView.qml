// Settings dialog (Phase 6a). Mirrors ui/qt/settings_dialog.py: Game /
// Sources / Profiles / Troubleshooting tabs. Reads the `settingsModel`
// context property (ui/qml/settings.py:SettingsModel). Profile import and
// the Linux (UMU) window land with the wizard dialogs (Phase 6b).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlSettingsDialog"
    title: "Settings"
    modal: true
    width: 600
    height: 660
    anchors.centerIn: parent

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
                ColumnLayout {
                    width: parent.width
                    spacing: 8
                    Label {
                        text: "GAME FOLDER"
                        font.bold: true
                        font.pointSize: 10
                        color: appTheme.colors["C_GOLD"]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            objectName: "qmlSettingsPath"
                            Layout.fillWidth: true
                            text: settingsModel.gamePath
                            readOnly: true
                            placeholderText: settingsModel.gameSuggestion !== "" ? settingsModel.gameSuggestion : "Select the game folder containing WoW.exe"
                            font.family: "monospace"
                        }
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
                        onCheckedChanged: settingsModel.setClearWdb(checked)
                    }
                    CheckBox {
                        objectName: "qmlSettingsCloseOnLaunch"
                        text: "Close Nostalgia Launcher on game launch"
                        visible: settingsModel.canLaunch
                        checked: settingsModel.closeOnLaunch
                        onCheckedChanged: settingsModel.setCloseOnLaunch(checked)
                    }
                    CheckBox {
                        objectName: "qmlSettingsClientUpdate"
                        text: "Enable client updates"
                        checked: settingsModel.clientUpdates
                        onCheckedChanged: settingsModel.setClientUpdates(checked)
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ── Sources ─────────────────────────────────────────
            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
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
                        onCheckedChanged: settingsModel.setAddonsDefault(checked)
                    }
                    CheckBox {
                        text: "Use community default mods catalog"
                        enabled: settingsModel.modsDefaultAvail
                        checked: settingsModel.modsDefault && settingsModel.modsDefaultAvail
                        onCheckedChanged: settingsModel.setModsDefault(checked)
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
                ColumnLayout {
                    width: parent.width
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
                            model: settingsModel.profiles
                            currentIndex: Math.max(0, settingsModel.profiles.indexOf(settingsModel.activeProfile))
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
                ColumnLayout {
                    width: parent.width
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

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Close
    }

    FileDialog {
        id: folderPicker
        objectName: "qmlSettingsFolderPicker"
        title: "Select game client folder"
        fileMode: FileDialog.OpenDirectory
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
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: parent.label
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
                Layout.preferredWidth: 64
            }
            TextField {
                id: urlField
                Layout.fillWidth: true
                text: parent.url
                font.family: "monospace"
            }
            Button {
                text: "Apply"
                onClicked: parent.apply(urlField.text)
            }
            Button {
                text: "Reset"
                flat: true
                ToolTip.text: "Use the default server catalog"
                ToolTip.visible: hovered
                onClicked: parent.reset()
            }
            Button {
                text: "Reload"
                flat: true
                ToolTip.text: "Fetch the catalog now and refresh the tab"
                ToolTip.visible: hovered
                onClicked: parent.reload()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.preferredWidth: 64 }
            Button {
                text: "Open custom file"
                flat: true
                onClicked: parent.openCustom()
            }
            Button {
                text: "Clear custom entries"
                flat: true
                onClicked: parent.clearCustom()
            }
        }
    }
}

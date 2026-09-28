// Linux (UMU) settings dialog (Phase 6b). Mirrors
// ui/qt/linux_settings_dialog.py. Reads the `linuxModel` context property
// (ui/qml/linux.py:LinuxModel). Opened from the Settings Game tab on Linux.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlLinuxSettingsDialog"
    title: "Linux (UMU) Settings"
    modal: true
    width: 560
    height: 560
    anchors.centerIn: parent

    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        Label {
            anchors.fill: parent
            anchors.leftMargin: 18
            verticalAlignment: Text.AlignVCenter
            text: "LINUX (UMU)"
            font.bold: true
            font.pointSize: 10
            color: appTheme.colors["C_GOLD"]
        }
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 8
            Label {
                objectName: "qmlLinuxUmuHint"
                Layout.fillWidth: true
                text: linuxModel.umuHint
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
            }
            Label {
                text: "Proton"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
            }
            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: protonCombo
                    objectName: "qmlLinuxProton"
                    Layout.fillWidth: true
                    editable: true
                    model: linuxModel.protonOptions
                    Component.onCompleted: currentIndex = Math.max(0, find(linuxModel.proton))
                }
                Button {
                    text: "Apply"
                    onClicked: linuxModel.applyProton(protonCombo.currentText)
                }
            }
            Label {
                text: "Renderer"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
            }
            RowLayout {
                Layout.fillWidth: true
                ComboBox {
                    id: rendererCombo
                    objectName: "qmlLinuxRenderer"
                    Layout.fillWidth: true
                    model: linuxModel.rendererOptions
                    Component.onCompleted: currentIndex = Math.max(0, find(linuxModel.renderer))
                }
                Button {
                    text: "Apply"
                    onClicked: linuxModel.applyRenderer(rendererCombo.currentText)
                }
            }
            CheckBox {
                text: "Client-folder DXVK (skip Proton's built-in)"
                checked: linuxModel.dxvk
                onToggled: linuxModel.setDxvk(checked)
            }
            CheckBox {
                text: "GameMode"
                enabled: linuxModel.gamemodeAvail
                checked: linuxModel.gamemode && linuxModel.gamemodeAvail
                onToggled: linuxModel.setGamemode(checked)
            }
            Label {
                Layout.fillWidth: true
                text: "gamemoderun not found — install GameMode to enable."
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
                visible: !linuxModel.gamemodeAvail
                wrapMode: Text.WordWrap
            }
            CheckBox {
                text: "Wayland backend"
                enabled: linuxModel.waylandAvail
                checked: linuxModel.wayland && linuxModel.waylandAvail
                onToggled: linuxModel.setWayland(checked)
            }
            Label {
                Layout.fillWidth: true
                text: "Not running on a Wayland session."
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
                visible: !linuxModel.waylandAvail
                wrapMode: Text.WordWrap
            }
            Label {
                text: "GAMEID"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: gameIdField
                    Layout.fillWidth: true
                    text: linuxModel.gameId
                    font.family: "monospace"
                }
                Button {
                    text: "Apply"
                    onClicked: linuxModel.applyGameId(gameIdField.text)
                }
            }
            Label {
                text: "umu-run"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: umuPathField
                    Layout.fillWidth: true
                    text: linuxModel.umuPath
                    placeholderText: "auto-detect on PATH"
                    font.family: "monospace"
                }
                Button {
                    text: "Browse…"
                    onClicked: umuPicker.open()
                }
                Button {
                    text: "Apply"
                    onClicked: linuxModel.applyUmuPath(umuPathField.text)
                }
            }
            Item { Layout.fillHeight: true }
        }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Close
    }

    FileDialog {
        id: umuPicker
        title: "Select umu-run binary"
        fileMode: FileDialog.OpenFile
        onAccepted: linuxModel.applyUmuPath(currentFile.toString().replace(/^file:\/\//, ""))
    }
}

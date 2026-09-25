// Client update progress (Phase 4):
// title + force-recheck, phase, progress bar (visible only in flight),
// current file, Method/Progress/Speed/Peers grid, updated-files list.
// Reads the `updateState` context property (ui/qml/viewmodels.py).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ScrollView {
    id: root
    objectName: "qmlUpdateView"
    clip: true

    ColumnLayout {
        width: root.width
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            Layout.topMargin: 28
            Label {
                objectName: "qmlUpdateTitle"
                Layout.fillWidth: true
                text: "CLIENT UPDATE"
                font.bold: true
                font.pointSize: 16
                color: appTheme.colors["C_GOLD_LT"]
            }
            ToolButton {
                objectName: "qmlUpdateRecheck"
                text: "⟳  Force recheck"
                Accessible.name: "Force recheck"
                ToolTip.text: "Re-verify every game file from scratch against the torrent snapshot's piece hashes"
                ToolTip.visible: hovered
                onClicked: updateState.recheck()
            }
        }

        Label {
            objectName: "qmlUpdatePhase"
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            text: updateState.phaseText
            font.pointSize: 11
            color: appTheme.colors["C_TEXT"]
        }

        ProgressBar {
            objectName: "qmlUpdateProgress"
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            from: 0
            to: 100
            value: updateState.progressValue * 100
            visible: updateState.progressVisible
        }

        Label {
            objectName: "qmlUpdateFile"
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            text: updateState.fileText
            font.pointSize: 10
            color: appTheme.colors["C_TEXT_DIM"]
            wrapMode: Text.WordWrap
        }

        ListView {
            id: fileList
            objectName: "qmlUpdateFileList"
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            Layout.preferredHeight: 120
            clip: true
            model: updateState.files
            delegate: Label {
                width: fileList.width
                text: (model.done ? "✓ " : "… ") + model.name
                font.pointSize: 9
                color: model.done ? appTheme.colors["C_OK"] : appTheme.colors["C_TEXT_DIM"]
                elide: Text.ElideMiddle
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 32
            Layout.rightMargin: 32
            columns: 2
            columnSpacing: 28
            rowSpacing: 8
            Label { text: "Method:"; font.bold: true; color: appTheme.colors["C_TEXT"] }
            Label {
                objectName: "qmlUpdateTransport"
                Layout.fillWidth: true
                text: updateState.transportText
                color: appTheme.colors["C_TEXT_DIM"]
            }
            Label { text: "Progress:"; font.bold: true; color: appTheme.colors["C_TEXT"] }
            Label {
                objectName: "qmlUpdateAmount"
                Layout.fillWidth: true
                text: updateState.amountText
                color: appTheme.colors["C_TEXT_DIM"]
            }
            Label { text: "Speed:"; font.bold: true; color: appTheme.colors["C_TEXT"] }
            Label {
                objectName: "qmlUpdateSpeed"
                Layout.fillWidth: true
                text: updateState.speedText
                color: appTheme.colors["C_TEXT_DIM"]
            }
            Label { text: "Peers:"; font.bold: true; color: appTheme.colors["C_TEXT"] }
            Label {
                objectName: "qmlUpdatePeers"
                Layout.fillWidth: true
                text: updateState.peersText
                color: appTheme.colors["C_TEXT_DIM"]
            }
        }

        Item { Layout.fillHeight: true }
    }
}

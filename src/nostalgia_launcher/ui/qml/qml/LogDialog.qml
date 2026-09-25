// Session-log viewer (Phase 6a): a scrollable
// monospace view of the retained session log with live tailing. Reads the
// `logModel` context property (ui/qml/settings.py:LogModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "qmlLogDialog"
    title: "Session log"
    visible: false
    width: 720
    height: 480
    minimumWidth: 480
    minimumHeight: 300
    color: appTheme.colors["C_BG"]
    Material.theme: Material.Dark
    Material.accent: appTheme.colors["C_GOLD"]

    onVisibleChanged: logDialogVisible(visible)

    signal logDialogVisible(bool open)

    // Single-instance raise (same pattern as SettingsView.showSettings).
    function showLog() {
        if (!visible)
            show();
        requestActivate();
        raise();
    }

    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        Label {
            anchors.fill: parent
            anchors.leftMargin: 18
            verticalAlignment: Text.AlignVCenter
            text: "SESSION LOG"
            font.bold: true
            font.pointSize: 13
            color: appTheme.colors["C_GOLD_LT"]
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
                text: "Refresh"
                flat: true
                onClicked: logModel.refresh()
            }
            Button {
                objectName: "qmlLogClose"
                text: "Close"
                onClicked: root.close()
            }
        }
    }

    ScrollView {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        clip: true
        contentWidth: availableWidth
        ListView {
            id: logList
            objectName: "qmlLogList"
            width: parent.width
            model: logModel.lines
            delegate: Label {
                width: ListView.view.width - 16
                text: modelData
                font.family: "monospace"
                font.pointSize: 9
                color: appTheme.colors["C_TEXT"]
                wrapMode: Text.WrapAnywhere
            }
            onCountChanged: logList.positionViewAtEnd()
        }
    }
}

// Session-log viewer (Phase 6a). Mirrors ui/qt/log_window.py: a scrollable
// monospace view of the retained session log with live tailing. Reads the
// `logModel` context property (ui/qml/settings.py:LogModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlLogDialog"
    title: "Session log"
    modal: false
    width: 720
    height: 480
    anchors.centerIn: parent

    onVisibleChanged: logDialogVisible(visible)

    signal logDialogVisible(bool open)

    ColumnLayout {
        anchors.fill: parent
        spacing: 4
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ListView {
                id: logList
                objectName: "qmlLogList"
                model: logModel.lines
                delegate: Label {
                    width: logList.width
                    text: modelData
                    font.family: "monospace"
                    font.pointSize: 9
                    color: appTheme.colors["C_TEXT"]
                    wrapMode: Text.WrapAnywhere
                }
                onCountChanged: logList.positionViewAtEnd()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                text: "Refresh"
                flat: true
                onClicked: logModel.refresh()
            }
            Button {
                text: "Close"
                onClicked: root.close()
            }
        }
    }
}

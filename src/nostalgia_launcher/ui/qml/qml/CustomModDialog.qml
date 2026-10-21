// ADD CUSTOM MOD dialog (Phase 6b). Mirrors ui/qt/custom_mod_dialog.py:
// kind/type-conditional fields, extract map, validation error. Reads the
// `customModModel` context property (ui/qml/custom.py:CustomModModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlCustomModDialog"
    title: "ADD CUSTOM MOD"
    modal: true
    width: Math.min(600, parent.width * 0.9)
    height: Math.min(640, parent.height * 0.9)
    anchors.centerIn: parent

    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        Label {
            anchors.fill: parent
            anchors.leftMargin: 18
            verticalAlignment: Text.AlignVCenter
            text: "ADD CUSTOM MOD"
            font.bold: true
            font.pointSize: appTheme.fonts["dialog"]
            color: appTheme.colors["C_GOLD_LT"]
        }
    }

    CustomModForm {
        anchors.fill: parent
        onAccepted: root.accept()
        onRejected: root.reject()
    }
}

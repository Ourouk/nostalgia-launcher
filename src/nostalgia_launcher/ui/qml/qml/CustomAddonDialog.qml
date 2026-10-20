// ADD CUSTOM GIT ADDON dialog (Phase 6b). Form lives in
// CustomAddonForm.qml (shared with the Browse dialog Custom page).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlCustomAddonDialog"
    title: "ADD CUSTOM GIT ADDON"
    modal: true
    width: 560
    height: 320
    anchors.centerIn: parent

    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        Label {
            anchors.fill: parent
            anchors.leftMargin: 18
            verticalAlignment: Text.AlignVCenter
            text: "ADD CUSTOM GIT ADDON"
            font.bold: true
            font.pointSize: 12
            color: appTheme.colors["C_GOLD_LT"]
        }
    }

    CustomAddonForm {
        anchors.fill: parent
        onAccepted: root.accept()
        onRejected: root.reject()
    }
}

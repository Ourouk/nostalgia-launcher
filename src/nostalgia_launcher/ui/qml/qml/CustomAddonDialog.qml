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
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 18
            anchors.rightMargin: 8
            Label {
                Layout.fillWidth: true
                verticalAlignment: Text.AlignVCenter
                text: "ADD CUSTOM GIT ADDON"
                font.bold: true
                font.pointSize: appTheme.fonts["dialog"]
                color: appTheme.colors["C_GOLD_LT"]
                elide: Text.ElideRight
            }
            ToolButton {
                objectName: "qmlCustomAddonClose"
                text: "✕"
                Accessible.name: "Close"
                ToolTip.text: "Close"
                ToolTip.visible: hovered
                onClicked: root.reject()
            }
        }
    }

    CustomAddonForm {
        anchors.fill: parent
        onAccepted: root.accept()
        onRejected: root.reject()
    }
}

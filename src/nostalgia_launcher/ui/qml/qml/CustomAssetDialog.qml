// ADD CUSTOM ASSET dialog (Phase 6b). Mirrors
// ui/qt/custom_asset_dialog.py. Reads `customAssetModel`
// (ui/qml/custom.py:CustomAssetModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlCustomAssetDialog"
    title: "ADD CUSTOM ASSET"
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
            text: "ADD CUSTOM ASSET"
            font.bold: true
            font.pointSize: appTheme.fonts["dialog"]
            color: appTheme.colors["C_GOLD_LT"]
        }
    }

    CustomAssetForm {
        anchors.fill: parent
        onAccepted: root.accept()
        onRejected: root.reject()
    }
}

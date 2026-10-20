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
    width: 600
    height: 640
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
            font.pointSize: 12
            color: appTheme.colors["C_GOLD_LT"]
        }
    }

    CustomAssetForm {
        anchors.fill: parent
        onAccepted: root.accept()
        onRejected: root.reject()
    }
}

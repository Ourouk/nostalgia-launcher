// ADD CUSTOM GIT ADDON dialog (Phase 6b). Mirrors
// ui/qt/custom_addon_dialog.py. Reads `customAddonModel`
// (ui/qml/custom.py:CustomAddonModel).

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

    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        Label {
            text: "REPOSITORY URL"
            font.bold: true
            font.pointSize: 9
            color: appTheme.colors["C_GOLD"]
        }
        TextField {
            objectName: "qmlCustomAddonUrl"
            Layout.fillWidth: true
            font.family: "monospace"
            onTextChanged: customAddonModel.setUrl(text)
        }
        Label {
            objectName: "qmlCustomAddonHint"
            Layout.fillWidth: true
            text: customAddonModel.hostsHint + "\nInstalls into Interface/AddOns/<folder>."
            color: appTheme.colors["C_TEXT_DIM"]
            wrapMode: Text.WordWrap
        }
        Label {
            objectName: "qmlCustomAddonError"
            Layout.fillWidth: true
            visible: customAddonModel.errorText !== ""
            text: customAddonModel.errorText
            color: appTheme.colors["C_ERR"]
            wrapMode: Text.WordWrap
        }
        Item { Layout.fillHeight: true }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button {
                text: "Cancel"
                flat: true
                onClicked: root.reject()
            }
            Button {
                objectName: "qmlCustomAddonInstall"
                text: "Install"
                highlighted: true
                onClicked: {
                    if (customAddonModel.submit())
                        root.accept();
                }
            }
        }
    }
}

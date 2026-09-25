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

    ScrollView {
        anchors.fill: parent
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 4
            Field { caption: "ASSET ID"; objectName: "qmlCustomAssetId"; onEdit: (t) => customAssetModel.setField("assetId", t) }
            Field { caption: "NAME (defaults to the id)"; onEdit: (t) => customAssetModel.setField("name", t) }
            Field { caption: "DOWNLOAD URL (https)"; onEdit: (t) => customAssetModel.setField("url", t) }
            Field { caption: "DESTINATION PATH (relative to the game folder)"; hint: "Data/patch.mpq"; onEdit: (t) => customAssetModel.setField("dest", t) }
            Field { caption: "SHA-1 (optional, 40 hex digits)"; onEdit: (t) => customAssetModel.setField("sha1", t) }
            Field { caption: "SIZE IN BYTES (optional)"; onEdit: (t) => customAssetModel.setField("size", t) }
            Field { caption: "VERSION TAG (optional)"; onEdit: (t) => customAssetModel.setField("version", t) }
            Field { caption: "REPO URL (optional https link)"; onEdit: (t) => customAssetModel.setField("repoUrl", t) }
            CheckBox {
                text: "Essential (auto-install)"
                onToggled: customAssetModel.setFlag("essential", checked)
            }
            CheckBox {
                text: "Probe the remote file for updates (drift detection)"
                onToggled: customAssetModel.setFlag("probe", checked)
            }
            Label {
                Layout.fillWidth: true
                text: "Assets are server content patches and install under the client's Data/ folder."
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
            }
            Label {
                objectName: "qmlCustomAssetError"
                Layout.fillWidth: true
                visible: customAssetModel.errorText !== ""
                text: customAssetModel.errorText
                color: appTheme.colors["C_ERR"]
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "Cancel"
                    flat: true
                    onClicked: root.reject()
                }
                Button {
                    objectName: "qmlCustomAssetSubmit"
                    text: "Add asset"
                    highlighted: true
                    onClicked: {
                        if (customAssetModel.submit())
                            root.accept();
                    }
                }
            }
        }
    }

    component Field: ColumnLayout {
        signal edit(string text)
        property alias caption: cap.text
        property string hint: ""
        Layout.fillWidth: true
        spacing: 2
        Label {
            id: cap
            font.bold: true
            font.pointSize: 9
            color: appTheme.colors["C_GOLD"]
        }
        TextField {
            Layout.fillWidth: true
            font.family: "monospace"
            placeholderText: parent.hint
            onTextChanged: parent.edit(text)
        }
    }
}

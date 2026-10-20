// CUSTOM GIT ADDON form: fields shared by CustomAddonDialog and the
// Browse dialog Custom page. Reads `customAddonModel`
// (ui/qml/custom.py:CustomAddonModel); emits accepted/rejected instead
// of closing anything itself.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ColumnLayout {
    signal accepted()
    signal rejected()

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
            onClicked: rejected()
        }
        Button {
            objectName: "qmlCustomAddonInstall"
            text: "Install"
            highlighted: true
            onClicked: {
                if (customAddonModel.submit())
                    accepted();
            }
        }
    }
}

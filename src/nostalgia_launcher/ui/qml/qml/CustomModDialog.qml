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
            text: "ADD CUSTOM MOD"
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

            Field { caption: "MOD ID"; objectName: "qmlCustomModId"; onEdit: (t) => customModModel.setField("modId", t) }
            Field { caption: "NAME (defaults to the id)"; onEdit: (t) => customModModel.setField("name", t) }
            Field { caption: "REPO URL (optional https link)"; onEdit: (t) => customModModel.setField("repoUrl", t) }
            Field { caption: "DESCRIPTION (optional)"; onEdit: (t) => customModModel.setField("description", t) }

            Label {
                text: "TYPE"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_GOLD"]
            }
            ComboBox {
                id: typeCombo
                objectName: "qmlCustomModType"
                Layout.fillWidth: true
                model: customModModel.modTypes
                onCurrentTextChanged: customModModel.setField("modType", currentText)
            }
            Label {
                text: "INSTALLATION"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_GOLD"]
            }
            ComboBox {
                Layout.fillWidth: true
                model: customModModel.installations
                onCurrentTextChanged: customModModel.setField("installation", currentText)
            }
            Field {
                caption: "GAME EXECUTABLE (relative to the game folder)"
                visible: typeCombo.currentText === "external-launcher"
                onEdit: (t) => customModModel.setField("executable", t)
            }
            Field { caption: "CLIENT VERSIONS (comma-separated, optional metadata)"; onEdit: (t) => customModModel.setField("clientVersions", t) }

            Label {
                text: "SOURCE KIND"
                font.bold: true
                font.pointSize: 9
                color: appTheme.colors["C_GOLD"]
            }
            ComboBox {
                id: kindCombo
                objectName: "qmlCustomModKind"
                Layout.fillWidth: true
                model: customModModel.sourceKinds
                onCurrentTextChanged: customModModel.setField("kind", currentText)
            }

            Field { caption: "OWNER"; visible: kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"; onEdit: (t) => customModModel.setField("owner", t) }
            Field { caption: "REPOSITORY"; visible: kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"; onEdit: (t) => customModModel.setField("repo", t) }
            Field { caption: "RELEASE ASSET PATTERN (fnmatch)"; visible: kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"; onEdit: (t) => customModModel.setField("pattern", t) }
            Field { caption: "PREFER ASSETS WITHOUT SUBSTRING (optional)"; visible: kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"; onEdit: (t) => customModModel.setField("preferNo", t) }
            CheckBox {
                text: "Derive the version from the matched asset name"
                visible: kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"
                onToggled: customModModel.setFlag("versionFromAsset", checked)
            }
            Field { caption: "FILE URL (https)"; visible: !(kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"); onEdit: (t) => customModModel.setField("fileUrl", t) }
            Field { caption: "DESTINATION PATH (relative to the game folder)"; visible: !(kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"); onEdit: (t) => customModModel.setField("dest", t) }
            Field { caption: "PINNED VERSION (optional)"; visible: !(kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release"); onEdit: (t) => customModModel.setField("pinnedVersion", t) }

            Label {
                text: "Extract map — one \"zip-pattern=dest/path\" line per entry:"
                font.bold: true
                color: appTheme.colors["C_GOLD"]
                visible: !(kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release")
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 64
                visible: !(kindCombo.currentText === "github_release" || kindCombo.currentText === "codeberg_release")
                TextArea {
                    objectName: "qmlCustomModExtractMap"
                    placeholderText: "ExampleMod.dll = ExampleMod.dll"
                    font.family: "monospace"
                    onTextChanged: customModModel.setField("extractMap", text)
                }
            }
            Label {
                Layout.fillWidth: true
                text: "Mods install into the game-folder root — DLLs land next to WoW.exe and get registered in dlls.txt."
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
            }
            Label {
                objectName: "qmlCustomModError"
                Layout.fillWidth: true
                visible: customModModel.errorText !== ""
                text: customModModel.errorText
                color: appTheme.colors["C_ERR"]
                wrapMode: Text.WordWrap
            }
        }
    }

    footer: DialogButtonBox {
        alignment: Qt.AlignRight
        Button {
            objectName: "qmlCustomModCancel"
            text: "Cancel"
            flat: true
            onClicked: root.reject()
        }
        Button {
            objectName: "qmlCustomModSubmit"
            text: "Add mod"
            highlighted: true
            onClicked: {
                if (customModModel.submit())
                    root.accept();
            }
        }
    }

    // One labelled line edit.
    component Field: ColumnLayout {
        signal edit(string text)
        property alias caption: cap.text
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
            onTextChanged: parent.edit(text)
        }
    }
}

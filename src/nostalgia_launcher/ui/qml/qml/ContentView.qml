// Shared MODS/ASSETS list (Phase 5). Mirrors ui/qt/content_panel.py:
// banner (title + search + All/Installed/Updates chips + reload), rows
// (checkbox, ★, name/version, retry/update action, repo link, description,
// error), empty state, ★-install/Apply footer. Instantiated per tab with a
// `contentModel` (ui/qml/content.py:ContentListModel) plus tab wording.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "qmlContentView"
    spacing: 0

    property var contentModel
    property string tabTitle: ""
    property string legendText: ""
    property string essentialText: ""
    property string customLabel: ""
    signal customRequested()

    ToolBar {
        Layout.fillWidth: true
        background: Rectangle {
            color: appTheme.colors["C_PANEL"]
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 12
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                Label {
                    objectName: "qmlContentTitle"
                    Layout.fillWidth: true
                    text: root.tabTitle
                    font.bold: true
                    font.pointSize: 12
                    color: appTheme.colors["C_GOLD"]
                }
                Label {
                    text: root.legendText
                    font.pointSize: 9
                    color: appTheme.colors["C_TEXT_DIM"]
                    visible: root.legendText !== ""
                }
                Button {
                    text: root.customLabel
                    visible: root.customLabel !== ""
                    flat: true
                    onClicked: root.customRequested()
                }
                ToolButton {
                    objectName: "qmlContentReload"
                    text: "⟳"
                    Accessible.name: "Reload catalog"
                    ToolTip.text: "Reload the catalog from the server"
                    ToolTip.visible: hovered
                    onClicked: root.contentModel.reloadCatalog()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    objectName: "qmlContentSearch"
                    Layout.fillWidth: true
                    placeholderText: "Filter…"
                    Accessible.name: "Filter entries"
                    onTextChanged: root.contentModel.setFilterText(text)
                }
                Button {
                    text: "All"
                    checkable: true
                    checked: root.contentModel.filterMode === "all"
                    flat: true
                    onClicked: root.contentModel.setFilterMode("all")
                }
                Button {
                    text: "Installed"
                    checkable: true
                    checked: root.contentModel.filterMode === "installed"
                    flat: true
                    onClicked: root.contentModel.setFilterMode("installed")
                }
                Button {
                    text: "Updates"
                    checkable: true
                    checked: root.contentModel.filterMode === "updates"
                    flat: true
                    onClicked: root.contentModel.setFilterMode("updates")
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        height: 1
        color: appTheme.colors["C_DIVIDER"]
    }

    Label {
        objectName: "qmlContentEmpty"
        Layout.fillWidth: true
        Layout.leftMargin: 16
        Layout.rightMargin: 16
        Layout.topMargin: 12
        visible: root.contentModel.emptyVisible
        text: root.contentModel.emptyText
        font.pointSize: 10
        color: appTheme.colors["C_TEXT_DIM"]
        wrapMode: Text.WordWrap
    }

    ListView {
        id: entryList
        objectName: "qmlContentList"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: root.contentModel
        delegate: ColumnLayout {
            width: entryList.width
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 12
                spacing: 6
                CheckBox {
                    Accessible.name: "Enable " + model.name
                    checked: model.enabled
                    onCheckedChanged: root.contentModel.toggleEntry(model.eid, checked)
                }
                Label {
                    text: "★"
                    font.pointSize: 11
                    color: appTheme.colors["C_GOLD"]
                    visible: model.required
                }
                Label {
                    Layout.fillWidth: true
                    text: model.name + "  " + model.version
                    font.bold: true
                    font.pointSize: 11
                    color: model.error !== "" ? appTheme.colors["C_ERR"] : (model.installed ? appTheme.colors["C_MOD_HL"] : appTheme.colors["C_TEXT"])
                    elide: Text.ElideRight
                }
                Button {
                    text: model.action === "retry" ? "Retry" : "Update"
                    visible: model.action !== ""
                    enabled: !root.contentModel.busy
                    flat: true
                    onClicked: root.contentModel.actOn(model.eid)
                }
                Button {
                    text: "⧉"
                    Accessible.name: "Open repository for " + model.name
                    visible: model.repoUrl !== ""
                    flat: true
                    onClicked: Qt.openUrlExternally(model.repoUrl)
                }
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                text: model.description
                font.pointSize: 10
                color: model.enabled ? appTheme.colors["C_TEXT"] : appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
                visible: model.description !== ""
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                text: model.error
                font.pointSize: 9
                color: appTheme.colors["C_ERR"]
                wrapMode: Text.WordWrap
                visible: model.error !== ""
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 6
                Layout.bottomMargin: 6
                height: 1
                color: appTheme.colors["C_DIVIDER"]
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        height: 1
        color: appTheme.colors["C_DIVIDER"]
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 16
        Layout.rightMargin: 16
        Layout.topMargin: 6
        Layout.bottomMargin: 10
        spacing: 8
        Button {
            objectName: "qmlContentEssential"
            text: root.essentialText
            enabled: root.contentModel.essentialEnabled
            onClicked: root.contentModel.installEssential()
        }
        Button {
            objectName: "qmlContentApply"
            text: root.contentModel.busy ? "Applying…" : "Apply"
            visible: root.contentModel.applyVisible
            enabled: !root.contentModel.busy
            highlighted: true
            onClicked: root.contentModel.applyAll()
        }
        Item { Layout.fillWidth: true }
    }
}

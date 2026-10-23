// Shared ADDONS/MODS/ASSETS manage list: banner (title + search +
// reload), rows (checkbox, name, repo link, description), empty state,
// Uninstall footer. Instantiated per tab with a `contentModel`
// (ui/qml/content.py:ContentListModel) plus tab wording.

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
    property string loadingText: "Loading…"

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
                    text: "INSTALLED " + root.tabTitle
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
                    placeholderText: "⌕  Filter"
                    Accessible.name: "Filter entries"
                    onTextChanged: root.contentModel.setFilterText(text)
                }
                Button {
                    objectName: "qmlContentBrowseMore"
                    text: "Add " + root.tabTitle.toLowerCase()
                    onClicked: root.contentModel.requestBrowse()
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: appTheme.colors["C_DIVIDER"]
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 16
        Layout.rightMargin: 16
        Layout.topMargin: 12
        spacing: 8
        visible: !root.contentModel.scanning && root.contentModel.emptyVisible
        Label {
            objectName: "qmlContentEmpty"
            Layout.fillWidth: true
            text: root.contentModel.emptyText
            font.pointSize: 10
            color: appTheme.colors["C_TEXT_DIM"]
            wrapMode: Text.WordWrap
        }
        Button {
            objectName: "qmlContentBrowse"
            text: "Browse " + root.tabTitle.toLowerCase()
            highlighted: true
            onClicked: root.contentModel.requestBrowse()
        }
    }
    ColumnLayout {
        objectName: "qmlContentWaiting"
        Layout.fillWidth: true
        Layout.topMargin: 32
        spacing: 12
        visible: root.contentModel.scanning
        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: parent.visible
        }
        Label {
            Layout.alignment: Qt.AlignHCenter
            text: root.loadingText
            font.pointSize: 10
            color: appTheme.colors["C_TEXT_DIM"]
        }
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
                spacing: 4
                CheckBox {
                    Accessible.name: "Uninstall " + model.name
                    checked: model.enabled
                    enabled: !(model.serverManaged || false)
                    onCheckedChanged: root.contentModel.toggleEntry(model.eid, checked)
                }
                Label {
                    text: "●"
                    font.pointSize: 8
                    color: appTheme.colors["C_GOLD"]
                    visible: (model.staged || false) && !(model.serverManaged || false)
                    Accessible.name: "Marked for uninstall: " + model.name
                }
                Label {
                    text: "SERVER"
                    font.pointSize: 8
                    font.bold: true
                    color: appTheme.colors["C_GOLD"]
                    visible: model.serverManaged || false
                    Accessible.name: "Server-managed entry for " + model.name
                }
                Label {
                    Layout.fillWidth: true
                    text: model.name
                    font.bold: true
                    font.pointSize: 11
                    color: appTheme.colors["C_TEXT"]
                    elide: Text.ElideRight
                }
                Button {
                    text: "⧉"
                    font.pointSize: 15
                    Accessible.name: "Open repository for " + model.name
                    ToolTip.text: "Open repository in browser"
                    ToolTip.visible: hovered
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
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
                visible: model.description !== ""
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 3
                Layout.bottomMargin: 3
                Layout.preferredHeight: 1
                color: appTheme.colors["C_DIVIDER"]
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
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
            objectName: "qmlContentApply"
            text: root.contentModel.busy ? "Uninstalling…" : (root.contentModel.pendingCount > 0 ? "Uninstall (" + root.contentModel.pendingCount + ")" : "Uninstall")
            enabled: !root.contentModel.busy && root.contentModel.pendingCount > 0
            highlighted: true
            onClicked: root.contentModel.applyAll()
        }
        Item { Layout.fillWidth: true }
    }
}

// Shared MODS/ASSETS list (Phase 5):
// banner (title + search + All/Installed/Updates chips + reload), rows
// (checkbox, name/version, retry/update action, repo link, description,
// error), empty state, Apply footer. Instantiated per tab with a
// `contentModel` (ui/qml/content.py:ContentListModel) plus tab wording.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "qmlContentView"
    spacing: 0

    property var contentModel
    property string tabTitle: ""
    property string legendText: ""
    property string customLabel: ""
    property string loadingText: "Loading…"
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
                Button {
                    objectName: "qmlContentUpdatesOnly"
                    text: "Updates only"
                    checkable: true
                    checked: root.contentModel.filterMode === "updates"
                    flat: true
                    onClicked: root.contentModel.setFilterMode(checked ? "updates" : "all")
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
        header: ExtraBlock {
            objectName: "qmlExtrasTop"
            showHeadline: true
            visible: root.contentModel.extrasOnTop && (root.contentModel.extraHeadline !== "" || root.contentModel.extraSections.length > 0)
        }
        footer: ExtraBlock {
            objectName: "qmlExtrasBottom"
            showHeadline: false
            visible: !root.contentModel.extrasOnTop && root.contentModel.extraSections.length > 0
        }
        delegate: ColumnLayout {
            width: entryList.width
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 12
                spacing: 4
                CheckBox {
                    Accessible.name: "Enable " + model.name
                    checked: model.enabled
                    enabled: !(model.serverManaged || false)
                    onCheckedChanged: root.contentModel.toggleEntry(model.eid, checked)
                }
                Label {
                    text: "●"
                    font.pointSize: 8
                    color: appTheme.colors["C_GOLD"]
                    visible: (model.staged || false) && !(model.serverManaged || false)
                    Accessible.name: "Unapplied change for " + model.name
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
                    text: model.name + "  " + model.version
                    font.bold: true
                    font.pointSize: 11
                    color: model.error !== "" ? appTheme.colors["C_ERR"] : (model.installed ? appTheme.colors["C_MOD_HL"] : appTheme.colors["C_TEXT"])
                    elide: Text.ElideRight
                }
                Button {
                    text: model.action === "retry" ? "Retry" : "Update"
                    visible: model.action !== "" && model.error === "" && !(model.serverManaged || false)
                    enabled: !root.contentModel.busy
                    flat: true
                    onClicked: root.contentModel.actOn(model.eid)
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
                color: model.enabled ? appTheme.colors["C_TEXT"] : appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
                visible: model.description !== ""
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                spacing: 8
                visible: model.error !== ""
                Label {
                    Layout.fillWidth: true
                    text: model.error
                    font.pointSize: 9
                    color: appTheme.colors["C_ERR"]
                    wrapMode: Text.WordWrap
                }
                Button {
                    text: model.action === "retry" ? "Retry" : "Update"
                    visible: model.action !== "" && !(model.serverManaged || false)
                    enabled: !root.contentModel.busy
                    flat: true
                    onClicked: root.contentModel.actOn(model.eid)
                }
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

    MessageDialog {
        id: extraConfirm
        title: "Remove file"
        text: root.contentModel.extraConfirmText
        buttons: MessageDialog.Yes | MessageDialog.No
        visible: root.contentModel.extraConfirmText !== ""
        onAccepted: root.contentModel.confirmExtra()
        onRejected: root.contentModel.cancelExtra()
    }

    // Extra sections: Data/ scan block (assets, top) or detected-DLL
    // block (mods, bottom). Instantiated as the list header/footer.
    component ExtraBlock: ColumnLayout {
        property bool showHeadline: false
        spacing: 2
        Label {
            objectName: "qmlExtrasHeadline"
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            Layout.topMargin: 6
            visible: showHeadline && root.contentModel.extraHeadline !== ""
            text: root.contentModel.extraHeadline
            font.pointSize: 9
            color: appTheme.colors["C_TEXT_DIM"]
            wrapMode: Text.WordWrap
        }
        Repeater {
            model: root.contentModel.extraSections
            ColumnLayout {
                width: parent.width
                spacing: 2
                property string sectionTitle: (modelData && modelData.title) || ""
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.topMargin: 10
                    text: (modelData && modelData.title) || ""
                    font.bold: true
                    color: modelData.color === "err" ? appTheme.colors["C_ERR"] : (modelData.color === "gold" ? appTheme.colors["C_GOLD"] : appTheme.colors["C_TEXT_DIM"])
                }
                Repeater {
                    model: modelData.rows
                    RowLayout {
                        width: parent.width
                        Layout.leftMargin: 16
                        Layout.rightMargin: 12
                        spacing: 8
                        Label {
                            Layout.fillWidth: true
                            text: (modelData && modelData.name || "") + ((modelData && modelData.meta) ? "  ·  " + modelData.meta : "")
                            font.pointSize: 9
                            color: modelData.action !== "" ? appTheme.colors["C_ERR"] : appTheme.colors["C_TEXT"]
                            elide: Text.ElideMiddle
                        }
                        Button {
                            text: modelData.action
                            visible: modelData.action !== ""
                            flat: true
                            onClicked: root.contentModel.requestExtra(sectionTitle, modelData.name)
                        }
                    }
                }
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
            text: root.contentModel.busy ? "Applying…" : (root.contentModel.pendingCount > 0 ? "Apply (" + root.contentModel.pendingCount + ")" : "Apply")
            enabled: !root.contentModel.busy && root.contentModel.applyVisible
            highlighted: true
            onClicked: root.contentModel.applyAll()
        }
        Item { Layout.fillWidth: true }
    }
}

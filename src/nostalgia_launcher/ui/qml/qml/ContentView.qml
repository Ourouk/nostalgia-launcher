// Shared MODS/ASSETS list (Phase 5):
// banner (title + search + All/Installed/Updates chips + reload), rows
// (checkbox, ★, name/version, retry/update action, repo link, description,
// error), empty state, ★-install/Apply footer. Instantiated per tab with a
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
    property string essentialText: ""
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
                ComboBox {
                    objectName: "qmlContentScanVersion"
                    visible: root.contentModel.scanVersions.length > 0
                    model: root.contentModel.scanVersions
                    Component.onCompleted: currentIndex = Math.max(0, find(root.contentModel.scanVersion))
                    ToolTip.text: "The client's game version — decides which Data/ archives count as stock"
                    ToolTip.visible: hovered
                    onCurrentTextChanged: root.contentModel.setScanVersion(currentText)
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
        visible: root.contentModel.emptyVisible && !root.contentModel.loading
        text: root.contentModel.emptyText
        font.pointSize: 10
        color: appTheme.colors["C_TEXT_DIM"]
        wrapMode: Text.WordWrap
    }

    ColumnLayout {
        objectName: "qmlContentWaiting"
        Layout.fillWidth: true
        Layout.topMargin: 32
        spacing: 12
        visible: root.contentModel.emptyVisible && root.contentModel.loading
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
                property string sectionTitle: modelData.title
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.topMargin: 10
                    text: modelData.title
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
                            text: modelData.name + (modelData.meta !== "" ? "  ·  " + modelData.meta : "")
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

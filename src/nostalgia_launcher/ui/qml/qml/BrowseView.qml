// BROWSE dialog: catalog search + one-click Install per row.
// Reads the `browseModel` property (ui/qml/browse.py:BrowseModel).
// Installed via the tab's existing apply-one path; no staging here.

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Dialog {
    id: root
    objectName: "qmlBrowseDialog"
    title: "BROWSE"
    modal: true
    // Fill the window: this is a working list, not a confirmation.
    width: parent.width
    height: parent.height
    anchors.centerIn: parent
    // Custom header: title + close share the title-bar row, so the X
    // costs no extra vertical space. (A button cannot be injected
    // into the native title bar, which this replaces at the same height.)
    header: ToolBar {
        background: Rectangle {
            color: appTheme.colors["C_PANEL"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 8
            Label {
                Layout.fillWidth: true
                text: root.title
                font.bold: true
                font.pointSize: 13
                color: appTheme.colors["C_TEXT"]
                elide: Text.ElideRight
            }
            ToolButton {
                objectName: "qmlBrowseClose"
                text: "✕"
                Accessible.name: "Close"
                ToolTip.text: "Close"
                ToolTip.visible: hovered
                onClicked: root.close()
            }
        }
    }
    bottomPadding: 4

    property var browseModel
    property string formSource: ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            TabBar {
                id: browseTabs
                TabButton { text: "Catalog" }
                TabButton { text: "Custom" }
            }
            TextField {
                objectName: "qmlBrowseSearch"
                Layout.fillWidth: true
                placeholderText: "Search"
                topPadding: 6
                bottomPadding: 6
                Accessible.name: "Search catalog"
                onTextChanged: root.browseModel.setFilterText(text)
            }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: browseTabs.currentIndex
            ColumnLayout {
                spacing: 6
                Label {
                    Layout.fillWidth: true
                    text: root.browseModel.emptyText
                    color: appTheme.colors["C_TEXT_DIM"]
                    visible: root.browseModel.emptyVisible
                    wrapMode: Text.WordWrap
                }
                ListView {
                    objectName: "qmlBrowseList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: 8
                    Layout.rightMargin: 8
                    clip: true
                    model: root.browseModel
                    delegate: Item {
                        width: ListView.view.width
                        height: rowCol.height
                        Rectangle {
                            anchors.fill: parent
                            color: appTheme.colors["C_TEXT"]
                            opacity: (index % 2) ? 0.08 : 0
                        }
                        ColumnLayout {
                            id: rowCol
                            anchors.left: parent.left
                            anchors.right: parent.right
                            spacing: 0
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Label {
                                Layout.fillWidth: true
                                text: model.entryName
                                font.bold: true
                                font.pointSize: 10
                                font.underline: titleHover.containsMouse
                                        && (model.entryRepoUrl || "") !== ""
                                color: (model.entryRepoUrl || "") !== ""
                                    ? appTheme.colors["C_GOLD"]
                                    : appTheme.colors["C_TEXT"]
                                elide: Text.ElideRight
                                Accessible.name: (model.entryRepoUrl || "") !== ""
                                    ? "Open repository for " + model.entryName
                                    : "Addon " + model.entryName
                                ToolTip.text: "Open repository in browser"
                                ToolTip.visible: titleHover.containsMouse
                                    && (model.entryRepoUrl || "") !== ""
                                MouseArea {
                                    id: titleHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: (model.entryRepoUrl || "") !== ""
                                        ? Qt.PointingHandCursor
                                        : Qt.ArrowCursor
                                    enabled: (model.entryRepoUrl || "") !== ""
                                    onClicked: Qt.openUrlExternally(model.entryRepoUrl)
                                }
                            }
                            CheckBox {
                                Accessible.name: "Select " + model.entryName
                                checked: model.entrySelected || false
                                topPadding: 0
                                bottomPadding: 0
                                onToggled: root.browseModel.setSelected(
                                    model.entryId, checked
                                )
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            Layout.topMargin: -4
                            text: model.entryDescription
                            font.pointSize: 9
                            color: appTheme.colors["C_TEXT"]
                            wrapMode: Text.WordWrap
                            maximumLineCount: 1
                            elide: Text.ElideRight
                            visible: model.entryDescription !== ""
                        }
                        }
                    }
                    }
            }
            Loader {
                id: formLoader
                Layout.fillWidth: true
                Layout.fillHeight: true
                source: root.formSource
            }
            Connections {
                target: formLoader.item
                function onAccepted() { browseTabs.currentIndex = 0; }
                function onRejected() { browseTabs.currentIndex = 0; }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label {
                Layout.fillWidth: true
                text: root.browseModel.selectedCount > 0
                    ? root.browseModel.selectedCount + " selected"
                    : ""
                color: appTheme.colors["C_TEXT_DIM"]
            }
            Button {
                objectName: "qmlBrowseInstall"
                text: root.browseModel.selectedCount > 0
                    ? "Install (" + root.browseModel.selectedCount + ")"
                    : "Install"
                Accessible.name: "Install selected entries"
                enabled: root.browseModel.selectedCount > 0
                    && !root.browseModel.busy
                highlighted: true
                topPadding: 6
                bottomPadding: 6
                onClicked: root.browseModel.installSelected()
            }
        }
    }
}


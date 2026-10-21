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
    // Scale with the window (was fixed 640x480 — cramped on big screens).
    width: Math.round(Math.min(parent.width * 0.85, 900))
    height: Math.round(Math.min(parent.height * 0.85, 700))
    anchors.centerIn: parent
    standardButtons: Dialog.Close

    property var browseModel
    property string formSource: ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        TabBar {
            id: browseTabs
            Layout.fillWidth: true
            TabButton { text: "Catalog" }
            TabButton { text: "Custom" }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: browseTabs.currentIndex
            ColumnLayout {
                spacing: 8
                TextField {
                    objectName: "qmlBrowseSearch"
                    Layout.fillWidth: true
                    placeholderText: "⌕  Search"
                    Accessible.name: "Search catalog"
                    onTextChanged: root.browseModel.setFilterText(text)
                }
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
                    clip: true
                    model: root.browseModel
                    delegate: ColumnLayout {
                        width: parent.width
                        spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                Layout.fillWidth: true
                                text: model.entryName + "  " + model.entryVersion
                                font.bold: true
                                color: appTheme.colors["C_TEXT"]
                                elide: Text.ElideRight
                            }
                            Button {
                                text: "Install"
                                Accessible.name: "Install " + model.entryName
                                enabled: !root.browseModel.busy
                                highlighted: true
                                onClicked: root.browseModel.install(model.entryId)
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: model.entryDescription
                            font.pointSize: 10
                            color: appTheme.colors["C_TEXT_DIM"]
                            wrapMode: Text.WordWrap
                            visible: model.entryDescription !== ""
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
    }
}


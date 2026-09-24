// ADDONS tab (Phase 5b). Mirrors ui/qt/addons_panel.py: legend + catalog
// age + check-for-updates + search header, NEED UPDATE / INSTALLED /
// AVAILABLE collapsible sections, rows (checkbox, ★, title, status action,
// repo link, description, error), ★-recommended/Apply/footer-label footer.
// Reads the `addonsModel` context property (ui/qml/addons.py:AddonsModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "qmlAddonsView"
    spacing: 0

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
                    Layout.fillWidth: true
                    text: "ADDONS   ★ recommended"
                    font.bold: true
                    font.pointSize: 12
                    color: appTheme.colors["C_GOLD"]
                }
                Label {
                    objectName: "qmlAddonsAge"
                    text: addonsModel.ageText
                    font.pointSize: 9
                    color: appTheme.colors["C_TEXT_DIM"]
                    visible: addonsModel.ageText !== ""
                }
                ToolButton {
                    objectName: "qmlAddonsCheck"
                    text: "⟳  Check for updates"
                    Accessible.name: "Check addons for updates"
                    ToolTip.text: "Re-check every addon against its repository (reloads the catalog too)"
                    ToolTip.visible: hovered
                    onClicked: addonsModel.checkForUpdates()
                }
            }
            TextField {
                objectName: "qmlAddonsSearch"
                Layout.fillWidth: true
                placeholderText: "⌕  Search addons"
                Accessible.name: "Search addons"
                onTextChanged: addonsModel.setFilterText(text)
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        height: 1
        color: appTheme.colors["C_DIVIDER"]
    }

    ListView {
        id: addonList
        objectName: "qmlAddonsList"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: addonsModel
        delegate: Loader {
            width: addonList.width
            sourceComponent: model.kind === "section" ? sectionComp : rowComp
            property string itemTitle: model.sectionTitle || ""
            property int itemCount: model.sectionCount || 0
            property bool itemOpen: model.sectionOpen || false
            property string itemEmpty: model.sectionEmpty || ""
            property string itemFolder: model.folder || ""
            property string itemRowTitle: model.rowTitle || ""
            property bool itemChecked: model.checked || false
            property bool itemRecommended: model.recommended || false
            property string itemStatusKind: model.statusKind || ""
            property string itemStatusText: model.statusText || ""
            property string itemRepoUrl: model.repoUrl || ""
            property string itemDescription: model.description || ""
            property string itemError: model.error || ""
        }
    }

    Component {
        id: sectionComp
        ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.topMargin: 10
                spacing: 4
                ToolButton {
                    text: itemOpen ? "▾" : "▸"
                    Accessible.name: itemTitle + " section"
                    flat: true
                    onClicked: addonsModel.toggleSection(itemTitle)
                }
                Label {
                    text: itemTitle
                    font.bold: true
                    font.pointSize: 11
                    color: appTheme.colors["C_GOLD"]
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: addonsModel.toggleSection(itemTitle)
                    }
                }
                Label {
                    text: "  " + itemCount
                    color: appTheme.colors["C_TEXT_DIM"]
                }
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                text: itemEmpty
                color: appTheme.colors["C_TEXT_DIM"]
                visible: itemEmpty !== ""
            }
        }
    }

    Component {
        id: rowComp
        ColumnLayout {
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 12
                spacing: 6
                CheckBox {
                    Accessible.name: "Install or remove " + itemFolder
                    checked: itemChecked
                    onCheckedChanged: addonsModel.toggleEntry(itemFolder, checked)
                }
                Label {
                    text: "★"
                    font.pointSize: 11
                    color: appTheme.colors["C_GOLD"]
                    visible: itemRecommended
                }
                Label {
                    objectName: "qmlAddonName_" + itemFolder
                    Layout.fillWidth: true
                    text: itemRowTitle
                    font.bold: true
                    font.pointSize: 11
                    color: appTheme.colors["C_TEXT"]
                    elide: Text.ElideRight
                }
                Button {
                    text: itemStatusText
                    visible: itemStatusKind === "retry" || itemStatusKind === "update"
                    flat: true
                    font.bold: itemStatusKind === "update"
                    onClicked: {
                        if (itemStatusKind === "retry")
                            addonsModel.checkForUpdates();
                        else
                            addonsModel.updateOne(itemFolder);
                    }
                }
                Label {
                    text: itemStatusText
                    visible: itemStatusKind !== "retry" && itemStatusKind !== "update" && itemStatusKind !== "none"
                    font.pointSize: 10
                    color: itemStatusKind === "error" ? appTheme.colors["C_ERR"] : (itemStatusKind === "warning" ? appTheme.colors["C_WARN"] : appTheme.colors["C_TEXT_DIM"])
                }
                Button {
                    text: "⧉"
                    Accessible.name: "Open repository for " + itemFolder
                    visible: itemRepoUrl !== ""
                    flat: true
                    onClicked: Qt.openUrlExternally(itemRepoUrl)
                }
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                text: itemDescription
                font.pointSize: 10
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
                visible: itemDescription !== ""
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                text: itemError
                font.pointSize: 9
                color: appTheme.colors["C_ERR"]
                wrapMode: Text.WordWrap
                visible: itemError !== ""
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
            objectName: "qmlAddonsRecommended"
            text: "★  Install Recommended"
            enabled: addonsModel.recommendedEnabled
            onClicked: addonsModel.installRecommended()
        }
        Item { Layout.fillWidth: true }
        Button {
            objectName: "qmlAddonsCustom"
            text: "+  Add custom git addon"
            flat: true
            onClicked: customAddonDialog.open()
        }
        Button {
            objectName: "qmlAddonsApply"
            text: addonsModel.busy ? "Applying…" : "Apply"
            visible: addonsModel.applyVisible
            enabled: !addonsModel.busy
            highlighted: true
            onClicked: addonsModel.applyAll()
        }
        Button {
            objectName: "qmlAddonsFooter"
            text: addonsModel.footerText
            enabled: addonsModel.footerClickable
            flat: true
            onClicked: addonsModel.updateAll()
        }
    }
}

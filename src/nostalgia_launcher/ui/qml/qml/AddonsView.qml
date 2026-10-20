// ADDONS tab (Phase 5b): legend + catalog
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
                    text: "INSTALLED ADDONS   ★ recommended"
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
            RowLayout {
                Layout.fillWidth: true
                spacing: 4
                TextField {
                    objectName: "qmlAddonsSearch"
                    Layout.fillWidth: true
                    placeholderText: "⌕  Search addons"
                    Accessible.name: "Search addons"
                    onTextChanged: addonsModel.setFilterText(text)
                }
                Button {
                    objectName: "qmlAddonsBrowseMore"
                    text: "Add add-ons"
                    onClicked: addonsModel.requestBrowse()
                }
                Button {
                    objectName: "qmlAddonsUpdatesOnly"
                    text: "Updates only"
                    checkable: true
                    checked: addonsModel.filterMode === "updates"
                    flat: true
                    onClicked: addonsModel.setFilterMode(checked ? "updates" : "all")
                }
            }
            Label {
                Layout.fillWidth: true
                text: "☐ off = remove on Apply · ● = unapplied change"
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
                elide: Text.ElideRight
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 1
        color: appTheme.colors["C_DIVIDER"]
    }

    ColumnLayout {
        objectName: "qmlAddonsWaiting"
        Layout.fillWidth: true
        Layout.topMargin: 32
        spacing: 12
        visible: addonsModel.loading && addonList.count === 0
        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: parent.visible
        }
        Label {
            Layout.alignment: Qt.AlignHCenter
            text: "Loading addons…"
            font.pointSize: 10
            color: appTheme.colors["C_TEXT_DIM"]
        }
        }
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 16
        Layout.rightMargin: 16
        Layout.topMargin: 12
        spacing: 8
        visible: !addonsModel.loading && addonList.count === 0
        Label {
            Layout.fillWidth: true
            text: "No add-ons installed yet."
            font.pointSize: 10
            color: appTheme.colors["C_TEXT_DIM"]
            wrapMode: Text.WordWrap
        }
        Button {
            objectName: "qmlAddonsBrowse"
            text: "Browse add-ons"
            highlighted: true
            onClicked: addonsModel.requestBrowse()
        }
    }

    ListView {
        id: addonList
        objectName: "qmlAddonsList"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: addonsModel
        delegate: ColumnLayout {
            width: addonList.width
            spacing: 0
            visible: (model.folder || "") !== ""
            // Addon row.
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 12
                spacing: 6
                CheckBox {
                    Accessible.name: "Install or remove " + model.folder
                    checked: model.checked || false
                    onCheckedChanged: addonsModel.toggleEntry(
                        model.folder, checked)
                }
                Label {
                    text: "●"
                    font.pointSize: 8
                    color: appTheme.colors["C_GOLD"]
                    visible: model.staged || false
                    Accessible.name: "Unapplied change for " + model.folder
                }
                Label {
                    text: "★"
                    font.pointSize: 11
                    color: appTheme.colors["C_GOLD"]
                    visible: model.recommended || false
                }
                Label {
                    // NOTE: role-derived objectNames are not registered
                    // for findChild (QTBUG) — tests read titles via the
                    // ListView delegates instead.
                    objectName: "qmlAddonName_" + model.folder
                    Accessible.name: "Addon " + model.folder
                    Layout.fillWidth: true
                    text: model.rowTitle || ""
                    font.bold: true
                    font.pointSize: 11
                    color: appTheme.colors["C_TEXT"]
                    elide: Text.ElideRight
                }
                Button {
                    text: model.statusText || ""
                    visible: (model.statusKind === "retry"
                        || model.statusKind === "update")
                        && (model.error || "") === ""
                        || model.statusKind === "update"
                    flat: true
                    font.bold: model.statusKind === "update"
                    onClicked: {
                        if (model.statusKind === "retry")
                            addonsModel.checkForUpdates();
                        else
                            addonsModel.updateOne(model.folder);
                    }
                }
                Label {
                    text: model.statusText || ""
                    visible: model.statusKind !== "retry"
                        && model.statusKind !== "update"
                        && model.statusKind !== "none"
                    font.pointSize: 10
                    color: model.statusKind === "error"
                        ? appTheme.colors["C_ERR"]
                        : (model.statusKind === "warning"
                            ? appTheme.colors["C_WARN"]
                            : appTheme.colors["C_TEXT_DIM"])
                }
                Button {
                    text: "⧉"
                    Accessible.name: "Open repository for " + model.folder
                    visible: (model.repoUrl || "") !== ""
                    flat: true
                    onClicked: Qt.openUrlExternally(model.repoUrl)
                }
            }
            Label {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                text: model.description || ""
                font.pointSize: 10
                color: appTheme.colors["C_TEXT_DIM"]
                wrapMode: Text.WordWrap
                visible: model.kind === "row" && (model.description || "") !== ""
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 48
                Layout.rightMargin: 12
                spacing: 8
                visible: model.kind === "row" && (model.error || "") !== ""
                Label {
                    Layout.fillWidth: true
                    text: model.error || ""
                    font.pointSize: 9
                    color: appTheme.colors["C_ERR"]
                    wrapMode: Text.WordWrap
                }
                Button {
                    text: model.statusKind === "retry" ? "Retry" : "Update"
                    visible: model.statusKind === "retry" || model.statusKind === "update"
                    flat: true
                    onClicked: {
                        if (model.statusKind === "retry")
                            addonsModel.checkForUpdates();
                        else
                            addonsModel.updateOne(model.folder);
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 6
                Layout.bottomMargin: 6
                Layout.preferredHeight: 1
                color: appTheme.colors["C_DIVIDER"]
                visible: model.kind === "row"
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
            objectName: "qmlAddonsRecommended"
            text: "★  Install Recommended"
            enabled: addonsModel.recommendedEnabled
            onClicked: addonsModel.installRecommended()
        }
        Item { Layout.fillWidth: true }
        Button {
            objectName: "qmlAddonsApply"
            text: addonsModel.busy ? "Applying…" : (addonsModel.pendingCount > 0 ? "Apply (" + addonsModel.pendingCount + ")" : "Apply")
            enabled: !addonsModel.busy && addonsModel.applyVisible
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

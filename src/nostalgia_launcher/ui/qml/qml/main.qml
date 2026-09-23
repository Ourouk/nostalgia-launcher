// Nostalgia Launcher QML shell (Phase 1: chrome only).
//
// Mirrors ui/qt/main_window.py: header wordmark, NEWS/UPDATE/ADDONS/MODS/
// ASSETS tabs, footer status + progress. Panels are placeholders until the
// per-tab migration lands; bindings already read the live view-models
// (`launcherState`, `appTheme`) exposed by ui/qml/app.py.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "qmlMainWindow"
    visible: false
    width: 1000
    height: 700
    title: "Nostalgia Launcher"
    color: appTheme.colors["C_BG"]

    header: ToolBar {
        objectName: "qmlHeader"
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Label {
                objectName: "qmlWordmark"
                text: "NOSTALGIA LAUNCHER"
                font.bold: true
                font.pointSize: 13
                color: appTheme.colors["C_GOLD_LT"]
            }
            Item { Layout.fillWidth: true }
            Label {
                objectName: "qmlVersionPill"
                text: "QML preview"
                font.pointSize: 9
                color: appTheme.colors["C_TEXT_DIM"]
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: navBar
            objectName: "qmlNavBar"
            Layout.fillWidth: true
            TabButton { text: "NEWS" }
            TabButton { text: "UPDATE" }
            TabButton { text: "ADDONS" }
            TabButton { text: "MODS" }
            TabButton { text: "ASSETS" }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: navBar.currentIndex

            Repeater {
                model: ["NEWS", "UPDATE", "ADDONS", "MODS", "ASSETS"]
                Rectangle {
                    // Placeholder page per tab (Phase 3+ replaces these
                    // with NewsView/UpdateView/… backed by list models).
                    color: appTheme.colors["C_PANEL"]
                    ColumnLayout {
                        anchors.centerIn: parent
                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: modelData
                            font.bold: true
                            font.pointSize: 16
                            color: appTheme.colors["C_GOLD_LT"]
                        }
                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: "QML view not migrated yet"
                            font.pointSize: 10
                            color: appTheme.colors["C_TEXT_DIM"]
                        }
                    }
                }
            }
        }
    }

    footer: ToolBar {
        objectName: "qmlFooter"
        background: Rectangle {
            color: appTheme.colors["C_HDR"]
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            Label {
                objectName: "qmlStatusLabel"
                Layout.fillWidth: true
                text: launcherState.statusText
                elide: Text.ElideRight
                font.pointSize: 10
                color: appTheme.colors["C_TEXT"]
            }
            ProgressBar {
                objectName: "qmlProgressBar"
                Layout.preferredWidth: 220
                from: 0
                to: 100
                value: launcherState.progressValue * 100
            }
            Button {
                objectName: "qmlPrimaryButton"
                text: "UPDATE"
            }
        }
    }
}

// Announcements list (Phase 3 pilot). Mirrors
// Announcements panel — header + refresh, status line,
// dated entries (date / title / author / body / link). Reads the
// `newsModel` context property (ui/qml/viewmodels.py:NewsFeedModel).
// Load failure (error, unconfigured, or empty feed) swaps the content
// column for a centered 404 + Play overlay (`newsModel.failed`).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

Item {
    id: root
    objectName: "qmlNewsView"

    // Normal content. Hidden wholesale on load failure (no per-item
    // visible bindings: the overlay below takes over instead, so no
    // layout reflow timing is involved).
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        visible: !newsModel.failed
        ToolBar {
            Layout.fillWidth: true
            background: Rectangle {
                color: appTheme.colors["C_PANEL"]
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 12
                Label {
                    Layout.fillWidth: true
                    text: "ANNOUNCEMENTS"
                    font.bold: true
                    font.pointSize: 12
                    color: appTheme.colors["C_GOLD"]
                }
                ToolButton {
                    objectName: "qmlNewsRefresh"
                    text: "⟳"
                    Accessible.name: "Refresh announcements"
                    ToolTip.text: "Refresh"
                    ToolTip.visible: hovered
                    onClicked: newsModel.refresh()
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: appTheme.colors["C_DIVIDER"]
        }
        Label {
            objectName: "qmlNewsStatus"
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.topMargin: 6
            visible: text !== ""
            text: newsModel.statusText
            font.pointSize: 9
            color: appTheme.colors["C_TEXT_DIM"]
        }
        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 6
            visible: newsModel.featuredTitle !== ""
            text: "★ " + newsModel.featuredTitle
            Accessible.name: "Featured announcement: " + newsModel.featuredTitle
            ToolTip.text: newsModel.featuredUrl !== "" ? "Open announcement in browser" : ""
            ToolTip.visible: featuredHover.containsMouse && newsModel.featuredUrl !== ""
            MouseArea {
                id: featuredHover
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                enabled: newsModel.featuredUrl !== ""
                onClicked: Qt.openUrlExternally(newsModel.featuredUrl)
            }
            font.bold: true
            font.pointSize: 11
            color: appTheme.colors["C_GOLD"]
            elide: Text.ElideRight
        }
        ListView {
            id: newsList
            objectName: "qmlNewsList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 4
            clip: true
            model: newsModel
            delegate: ColumnLayout {
                width: newsList.width
                spacing: 2
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    Layout.rightMargin: 14
                    text: model.date
                    font.pointSize: 9
                    color: appTheme.colors["C_TEXT_DIM"]
                    visible: model.date !== ""
                }
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    Layout.rightMargin: 14
                    text: model.title
                    font.bold: true
                    font.pointSize: 11
                    color: appTheme.colors["C_GOLD"]
                    wrapMode: Text.WordWrap
                }
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    Layout.rightMargin: 14
                    text: model.author
                    font.italic: true
                    font.pointSize: 10
                    color: appTheme.colors["C_TEXT_DIM"]
                    visible: model.author !== ""
                }
                Label {
                    Layout.fillWidth: true
                    Layout.leftMargin: 14
                    Layout.rightMargin: 14
                    text: model.body
                    font.pointSize: 10
                    color: appTheme.colors["C_TEXT"]
                    wrapMode: Text.WordWrap
                    visible: model.body !== ""
                }
                Button {
                    Layout.leftMargin: 14
                    flat: true
                    text: "Read more ⧉"
                    visible: model.url !== ""
                    onClicked: Qt.openUrlExternally(model.url)
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    Layout.bottomMargin: 6
                    Layout.preferredHeight: 1
                    color: appTheme.colors["C_DIVIDER"]
                }
            }
        }
    }

    // Load failure fallback: 404 + Play (same action as the footer).
    // Plain-Item overlay — `anchors.centerIn` needs no layout pass,
    // so the switch renders centered on the first frame.
    Item {
        anchors.fill: parent
        visible: newsModel.failed
        ColumnLayout {
            anchors.centerIn: parent
            spacing: 12
            Label {
                Layout.alignment: Qt.AlignHCenter
                text: "404"
                font.pointSize: 48
                font.bold: true
                color: appTheme.colors["C_GOLD"]
            }
            Label {
                Layout.alignment: Qt.AlignHCenter
                text: newsModel.statusText
                font.pointSize: 10
                color: appTheme.colors["C_TEXT_DIM"]
            }
            Button {
                Layout.alignment: Qt.AlignHCenter
                objectName: "qmlNewsPlay"
                text: updateState.primaryLabel
                enabled: updateState.primaryEnabled
                highlighted: true
                onClicked: updateState.primary()
            }
        }
    }
}

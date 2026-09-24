// Announcements list (Phase 3 pilot). Mirrors
// Announcements panel — header + refresh, status line,
// dated entries (date / title / author / body / link). Reads the
// `newsModel` context property (ui/qml/viewmodels.py:NewsFeedModel).

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "qmlNewsView"
    spacing: 0

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
        height: 1
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
        Layout.topMargin: 6
        visible: newsModel.featuredTitle !== ""
        text: "★ " + newsModel.featuredTitle
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
                height: 1
                color: appTheme.colors["C_DIVIDER"]
            }
        }
    }
}

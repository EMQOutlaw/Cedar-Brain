import QtQuick
import QtQuick.Layouts
import "../core"

ColumnLayout {
    id: root
    property string title: ""
    property string subtitle: ""
    spacing: 5
    CedarText {
        text: root.title
        font.pixelSize: 32
        font.weight: Font.DemiBold
        Layout.fillWidth: true
        opacity: 1
        onTextChanged: if (!Theme.reducedMotion)
            settle.restart()
        NumberAnimation on opacity {
            id: settle
            running: false
            from: .35
            to: 1
            duration: Theme.transition
        }
    }
    CedarText {
        text: root.subtitle
        color: Theme.muted
        Layout.fillWidth: true
    }
}

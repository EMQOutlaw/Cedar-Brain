pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "../core"

ComboBox {
    id: root
    implicitHeight: 40
    implicitWidth: 180
    font.family: Theme.labelFont
    font.pixelSize: 16
    palette.text: Theme.text
    palette.buttonText: Theme.text
    palette.button: Theme.surface
    palette.base: Theme.surface
    palette.highlight: Theme.border
    palette.highlightedText: Theme.text
    contentItem: CedarText {
        text: root.displayText
        leftPadding: 12
        rightPadding: 24
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
        elide: Text.ElideRight
    }
    background: ChamferFrame {
        cut: 6
        fill: Theme.background
        stroke: root.visualFocus ? Theme.green : Theme.border
        strokeWidth: root.visualFocus ? 2 : 1
    }
    delegate: ItemDelegate {
        id: option
        required property var modelData
        width: root.width
        text: root.textRole ? option.modelData[root.textRole] : option.modelData
        contentItem: CedarText {
            text: option.text
            elide: Text.ElideRight
            wrapMode: Text.NoWrap
        }
        background: Rectangle {
            color: option.highlighted ? Theme.elevated : Theme.surface
        }
    }
}

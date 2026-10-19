import QtQuick
import QtQuick.Controls
import "../core"

TextField {
    id: root
    implicitHeight: 40
    color: Theme.text
    font.family: Theme.labelFont
    font.pixelSize: 16
    placeholderTextColor: Theme.muted
    selectionColor: Theme.border
    selectedTextColor: Theme.text
    leftPadding: 12
    rightPadding: 12
    Accessible.name: placeholderText
    background: ChamferFrame {
        cut: 6
        fill: Theme.background
        stroke: root.activeFocus ? Theme.green : Theme.border
        strokeWidth: root.activeFocus ? 2 : 1
    }
}

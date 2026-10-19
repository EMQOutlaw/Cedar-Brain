import QtQuick
import QtQuick.Controls
import "../core"

Button {
    id: root
    property bool primary: false
    implicitHeight: 38
    implicitWidth: Math.max(80, contentItem.implicitWidth + 28)
    padding: 10
    focusPolicy: Qt.StrongFocus
    opacity: enabled ? 1 : .42
    Accessible.name: text
    contentItem: CedarText {
        text: root.text
        font.pixelSize: 15
        color: root.primary || root.checked ? Theme.green : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        wrapMode: Text.NoWrap
    }
    background: ChamferFrame {
        cut: 6
        fill: root.down ? Theme.elevated : root.checked || root.primary ? Theme.surface : Theme.background
        stroke: root.visualFocus ? Theme.green : Qt.alpha(Theme.accent, root.hovered || root.checked ? .55 : .22)
        strokeWidth: root.visualFocus ? 2 : 1
        line: root.primary || root.checked
        lineColor: Theme.accent
        lineFraction: root.down ? .85 : .4
        lineOpacity: .6
    }
}

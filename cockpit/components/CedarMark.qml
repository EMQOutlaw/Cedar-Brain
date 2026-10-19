import QtQuick
import QtQuick.Shapes
import "../core"

// Shared native tree mark, first drawn for The Journey.
Item {
    id: root
    property color ink: Theme.accent
    implicitWidth: 38
    implicitHeight: 43
    Shape {
        width: 38
        height: 43
        anchors.centerIn: parent
        scale: Math.min(root.width / 38, root.height / 43)
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.ink
            strokeWidth: 1
            fillColor: "transparent"
            PathSvg {
                path: "M19 1 L10 15 L15 15 L5 29 L16 29 L16 41 M19 1 L28 15 L23 15 L33 29 L22 29 L22 41 M19 13 L19 38"
            }
        }
    }
}

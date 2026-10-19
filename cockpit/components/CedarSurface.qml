import QtQuick
import "../core"

ChamferFrame {
    fill: Theme.surface
    stroke: Qt.alpha(Theme.accent, .22)
    cut: 10
    // Static grain: one cached tile, no animation or shell service dependencies.
    Image {
        anchors.fill: parent
        anchors.margins: 18
        source: "../assets/grain.png"
        fillMode: Image.Tile
        opacity: .035
    }
}

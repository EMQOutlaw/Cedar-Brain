import QtQuick
import "../core"

Text {
    textFormat: Text.PlainText
    color: Theme.text
    font.family: Theme.labelFont
    font.pixelSize: 16
    wrapMode: Text.Wrap
    Accessible.role: Accessible.StaticText
    Accessible.name: text
}

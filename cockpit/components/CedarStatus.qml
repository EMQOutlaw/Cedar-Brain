import QtQuick
import "../core"

CedarText {
    property string status: ""
    text: "• " + status.replace(/_/g, " ")
    color: Theme.statusColor(status)
    font.family: Theme.dataFont
    font.pixelSize: 12
}

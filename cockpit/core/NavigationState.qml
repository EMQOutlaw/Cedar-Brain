pragma Singleton
import QtQuick
import Quickshell

Singleton {
    property int page: 0
    readonly property var names: ["Pathfinder", "Cortex", "Spore Library", "Hyphae", "Agent Grove", "Settings"]
    property string inspectorTitle: ""
    property string inspectorText: ""
    property string inspectorKind: ""
    property string selection: ""
    property bool inspectorOpen: false
    function inspect(title, text, kind, id) {
        inspectorTitle = title;
        inspectorText = text;
        inspectorKind = kind || "";
        selection = id || "";
        inspectorOpen = true;
    }
    onPageChanged: inspectorOpen = false
}

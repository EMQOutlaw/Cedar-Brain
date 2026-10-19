pragma Singleton
import QtQuick
import QtCore
import Quickshell
import Quickshell.Io

Singleton {
    id: root
    property bool reducedMotion: false
    property bool syncTheme: true
    property string preferredAgent: "claude"
    property string defaultProject: ""
    Settings {
        category: "Mycelium"
        location: "file://" + (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/cedar-brain/frontend.ini"
        property alias reducedMotion: root.reducedMotion
        property alias syncTheme: root.syncTheme
        property alias preferredAgent: root.preferredAgent
        property alias defaultProject: root.defaultProject
    }
    property var palette: ({})
    function token(key, fallback) {
        const v = syncTheme ? palette[key] : null;
        return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v) ? v : fallback;
    }
    FileView {
        path: (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config") + "/cedar/theme.json"
        watchChanges: root.syncTheme
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try {
                root.palette = JSON.parse(text()).colors || {};
            } catch (_) {
                root.palette = {};
            }
        }
    }
    // CEDAR Shell palette and Strata token derivation, packaged independently.
    readonly property color background: token("background", "#080F0D")
    readonly property color surface: token("surface", "#101E19")
    readonly property color elevated: token("elevated", "#192D25")
    readonly property color border: token("border", "#315C4D")
    readonly property color green: token("green", "#9DFFB0")
    readonly property color teal: token("teal", "#3FE0C5")
    readonly property color amber: token("amber", "#F2C879")
    readonly property color ember: token("ember", "#E58B73")
    readonly property color text: token("text", "#E3F2E9")
    readonly property color muted: token("muted", "#A0B9AD")
    readonly property color accent: Qt.tint(muted, Qt.alpha(green, .24))
    readonly property string labelFont: Qt.fontFamilies().includes("Rajdhani") ? "Rajdhani" : "sans-serif"
    readonly property string dataFont: "monospace"
    readonly property int transition: reducedMotion ? 0 : 180
    readonly property int morphGrow: reducedMotion ? 0 : 320
    readonly property int morphShrink: reducedMotion ? 0 : 200
    function statusColor(s) {
        if (["failed", "interrupted", "error", "invalid"].includes(s))
            return ember;
        if (["awaiting_approval", "untrusted", "unsupported"].includes(s))
            return amber;
        if (["connected", "running", "succeeded", "available", "ready"].includes(s))
            return green;
        return muted;
    }
}

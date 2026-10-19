import QtQuick
import QtQuick.Layouts
import "../core"
import "../components"

CedarSurface {
    id: root
    property var agent: ({})
    implicitHeight: content.implicitHeight + 40
    ColumnLayout {
        id: content
        anchors {
            left: parent.left
            right: parent.right
            top: parent.top
            margins: 20
        }
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            CedarText {
                text: root.agent.name || "Agent"
                font.pixelSize: 26
                Layout.fillWidth: true
            }
            CedarStatus {
                status: root.agent.active_jobs > 0 ? "busy" : root.agent.status || "unknown"
            }
        }
        CedarText {
            Layout.fillWidth: true
            text: (root.agent.executable || "Executable not found") + "\n" + (root.agent.version || "No version detected")
            color: Theme.muted
        }
        CedarText {
            Layout.fillWidth: true
            text: root.agent.authenticated === true ? "Authentication verified by CLI check" : root.agent.authenticated === false ? "Authentication check failed · log in through the CLI" : "Authentication not verified"
            color: root.agent.authenticated === true ? Theme.accent : Theme.amber
        }
        CedarText {
            Layout.fillWidth: true
            text: (root.agent.active_jobs || 0) + " active jobs · " + (root.agent.modes || []).join(", ")
            color: Theme.muted
        }
        CedarText {
            visible: !!root.agent.detail
            Layout.fillWidth: true
            text: root.agent.detail || ""
            color: Theme.amber
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            CedarButton {
                text: "Sessions and details"
                enabled: BrainConnection.ready
                onClicked: BrainStore.inspectAgent(root.agent.id)
            }
            CedarButton {
                text: Theme.preferredAgent === root.agent.id ? "Preferred planner" : "Prefer for plans"
                checked: Theme.preferredAgent === root.agent.id
                onClicked: Theme.preferredAgent = root.agent.id
            }
        }
    }
}

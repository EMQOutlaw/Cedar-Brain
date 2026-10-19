import QtQuick
import QtQuick.Layouts
import "../core"
import "../components"

CedarSurface {
    id: root
    property var step: ({})
    property var definition: ({})
    property int number: 1
    property double now: Date.now() / 1000
    implicitHeight: content.implicitHeight + 32
    line: step.state === "running" || step.state === "succeeded"
    lineColor: Theme.statusColor(step.state || "")
    lineFraction: step.state === "running" ? .75 : .3
    ColumnLayout {
        id: content
        anchors {
            left: parent.left
            right: parent.right
            top: parent.top
            margins: 16
        }
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            CedarText {
                Layout.fillWidth: true
                text: root.number + "  " + (root.definition?.description || root.step.step_id)
                font.pixelSize: 20
            }
            CedarStatus {
                status: root.step.state || "pending"
            }
        }
        CedarText {
            Layout.fillWidth: true
            text: root.step.executor + " · " + (root.step.started_at ? (root.step.finished_at ? Math.max(0, root.step.finished_at - root.step.started_at).toFixed(1) + " s" : Math.max(0, root.now - root.step.started_at).toFixed(0) + " s elapsed") : "Not started")
            color: Theme.muted
            font.pixelSize: 13
        }
        CedarText {
            visible: !!root.step.error
            text: root.step.error?.message || ""
            Layout.fillWidth: true
            color: Theme.ember
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            CedarButton {
                text: "Inspect step"
                onClicked: BrainStore.inspectStep(root.step.step_id)
            }
            CedarButton {
                visible: root.step.state === "awaiting_approval"
                text: "Approve step"
                primary: true
                enabled: BrainStore.supports("hyphae.step_approval")
                onClicked: BrainStore.approveStep(root.step.step_id, true)
            }
            CedarButton {
                visible: root.step.state === "awaiting_approval"
                text: "Reject step"
                enabled: BrainStore.supports("hyphae.step_approval")
                onClicked: BrainStore.approveStep(root.step.step_id, false)
            }
        }
    }
}

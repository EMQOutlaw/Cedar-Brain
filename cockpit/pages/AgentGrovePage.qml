import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components"
import "../agents"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true
    ColumnLayout {
        width: page.availableWidth
        spacing: 20
        CedarHeader {
            Layout.fillWidth: true
            title: "Agent Grove"
            subtitle: "Installed tools. Explicit capabilities. One place to see their work."
        }
        CedarButton {
            text: "Check installed agents"
            enabled: BrainConnection.ready
            onClicked: BrainStore.refreshAgents(true)
        }
        Repeater {
            model: BrainStore.agents
            AgentCard {
                required property var modelData
                Layout.fillWidth: true
                agent: modelData
            }
        }
        CedarText {
            Layout.fillWidth: true
            visible: BrainStore.agents.length === 0
            text: "Connect to the backend to discover installed CLI agents."
            color: Theme.muted
        }
        CedarText {
            Layout.fillWidth: true
            text: "Job ownership and execution are managed by the backend. Select an agent to inspect sessions; open the owning workflow to view output or cancel."
            color: Theme.muted
        }
        Repeater {
            model: BrainStore.agentDetail?.recent_jobs || []
            CedarSurface {
                id: jobCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: job.implicitHeight + 28
                ColumnLayout {
                    id: job
                    anchors {
                        left: parent.left
                        right: parent.right
                        top: parent.top
                        margins: 14
                    }
                    CedarText {
                        Layout.fillWidth: true
                        text: jobCard.modelData.id + " · " + jobCard.modelData.status
                        font.family: Theme.dataFont
                        font.pixelSize: 12
                    }
                    CedarText {
                        Layout.fillWidth: true
                        text: "Owner: " + jobCard.modelData.owner_kind + " / " + jobCard.modelData.owner_id + "\nSession: " + (jobCard.modelData.session_id || "not assigned")
                        color: Theme.muted
                    }
                    CedarButton {
                        text: "Inspect job"
                        onClicked: NavigationState.inspect(jobCard.modelData.id, "Owner: " + jobCard.modelData.owner_kind + " / " + jobCard.modelData.owner_id + "\nState: " + jobCard.modelData.status + "\nMode: " + jobCard.modelData.mode + "\nSession: " + (jobCard.modelData.session_id || "not assigned") + "\nExit code: " + (jobCard.modelData.exit_code ?? "not available") + "\n\nOpen the owning run in Hyphae for structured output and supported cancellation. Protocol v1 does not expose standalone job output or cancellation methods.", "job", jobCard.modelData.id)
                    }
                    CedarButton {
                        text: "Execution history"
                        onClicked: {
                            NavigationState.page = 3;
                            BrainStore.refreshRuns();
                        }
                    }
                }
            }
        }
    }
}

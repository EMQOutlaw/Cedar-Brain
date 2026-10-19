pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components"
import "../workflows"
import "../core/Models.js" as Models

ColumnLayout {
    id: page
    property double now: Date.now() / 1000
    Timer {
        interval: 1000
        repeat: true
        running: page.visible && BrainConnection.ready && Models.active(BrainStore.run?.status)
        onTriggered: page.now = Date.now() / 1000
    }
    spacing: 16
    CedarHeader {
        Layout.fillWidth: true
        title: "Hyphae"
        subtitle: "Follow the work, from intention to verified result."
    }
    RowLayout {
        Layout.fillWidth: true
        CedarCombo {
            Layout.fillWidth: true
            model: BrainStore.runs.map(r => ({
                        id: r.id,
                        name: r.status + " · " + r.id
                    }))
            textRole: "name"
            currentIndex: BrainStore.runs.findIndex(r => r.id === BrainStore.run?.id)
            displayText: BrainStore.run?.id || "Choose a run from history"
            onActivated: index => BrainStore.selectRun(BrainStore.runs[index].id)
            enabled: BrainConnection.ready
        }
        CedarButton {
            text: "Refresh history"
            enabled: BrainConnection.ready
            onClicked: BrainStore.refreshRuns()
        }
    }
    RowLayout {
        visible: !!BrainStore.run
        Layout.fillWidth: true
        CedarText {
            text: BrainStore.workflow?.name || "Loading workflow…"
            Layout.fillWidth: true
            font.pixelSize: 24
        }
        CedarStatus {
            status: BrainStore.run?.status || ""
        }
        CedarButton {
            text: BrainStore.cancelling ? "Cancellation requested…" : "Cancel run"
            visible: Models.active(BrainStore.run?.status)
            enabled: BrainConnection.ready && !BrainStore.cancelling
            onClicked: BrainStore.cancelRun()
        }
    }
    CedarText {
        visible: !BrainConnection.ready && !!BrainStore.run
        text: "Offline · this is the last known execution state."
        color: Theme.amber
    }
    ScrollView {
        id: scroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: scroll.availableWidth
            spacing: 12
            Repeater {
                model: BrainStore.run?.steps || []
                ColumnLayout {
                    id: stepRow
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        visible: stepRow.index > 0
                        implicitWidth: 1
                        implicitHeight: 16
                        color: Theme.border
                        Layout.leftMargin: 28
                    }
                    WorkflowStep {
                        Layout.fillWidth: true
                        number: stepRow.index + 1
                        now: page.now
                        step: stepRow.modelData
                        definition: BrainStore.workflow?.definition?.steps?.find(s => s.id === stepRow.modelData.step_id) || {}
                    }
                }
            }
            CedarText {
                visible: !BrainStore.run
                Layout.fillWidth: true
                text: "No execution selected.\nReview a plan in Pathfinder, then approve it to begin."
                color: Theme.muted
                font.pixelSize: 24
                Layout.topMargin: 40
            }
            CedarText {
                visible: !!BrainStore.run?.error
                Layout.fillWidth: true
                text: Models.pretty(BrainStore.run?.error)
                color: Theme.ember
            }
            CedarText {
                visible: BrainStore.run?.status === "interrupted"
                Layout.fillWidth: true
                text: "The backend stopped during this run. Inspect results and any worktree before starting new work."
                color: Theme.amber
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import "../core"
import "../components"
import "../core/Models.js" as Models

ScrollView {
    id: page
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        width: page.availableWidth
        spacing: 20
        CedarHeader {
            Layout.fillWidth: true
            title: "Settings"
            subtitle: "A small set of preferences. A clear view of the system."
        }
        CedarText {
            text: "GENERAL"
            color: Theme.accent
            font.letterSpacing: 2
        }
        Flow {
            Layout.fillWidth: true
            spacing: 10
            CedarButton {
                text: Theme.syncTheme ? "CEDAR theme sync: on" : "CEDAR theme sync: off"
                checkable: true
                checked: Theme.syncTheme
                onClicked: Theme.syncTheme = checked
            }
            CedarButton {
                text: Theme.reducedMotion ? "Reduced motion: on" : "Reduced motion: off"
                checkable: true
                checked: Theme.reducedMotion
                onClicked: Theme.reducedMotion = checked
            }
            CedarButton {
                text: "Use current project by default"
                enabled: !!BrainStore.projectId
                onClicked: Theme.defaultProject = BrainStore.projectId
            }
        }
        CedarText {
            Layout.fillWidth: true
            text: "Default project: " + (BrainStore.projects.find(p => p.id === Theme.defaultProject)?.name || "Backend active project") + "\nPreferences are stored locally. Optional theme: $XDG_CONFIG_HOME/cedar/theme.json"
            color: Theme.muted
        }
        RowLayout {
            Layout.fillWidth: true
            CedarInput {
                id: projectPath
                Layout.fillWidth: true
                placeholderText: "Absolute path to an existing Git project"
            }
            CedarButton {
                text: "Add and select"
                enabled: BrainStore.supports("projects.registry") && projectPath.text.startsWith("/")
                onClicked: BrainStore.addProject(projectPath.text)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            CedarInput {
                id: graphPath
                Layout.fillWidth: true
                placeholderText: "Absolute path to Graphify graph.json"
            }
            CedarButton {
                text: "Set graph"
                enabled: BrainConnection.ready && !!BrainStore.projectId && graphPath.text.startsWith("/")
                onClicked: BrainStore.call("projects.set_graph", {
                    project_id: BrainStore.projectId,
                    graph_path: graphPath.text
                }, () => BrainStore.refreshProject())
            }
        }
        CedarText {
            text: "AGENTS"
            color: Theme.accent
            font.letterSpacing: 2
        }
        CedarCombo {
            model: ["claude", "codex"]
            currentIndex: Theme.preferredAgent === "codex" ? 1 : 0
            onActivated: index => Theme.preferredAgent = model[index]
            Accessible.name: "Preferred planning agent"
        }
        CedarText {
            Layout.fillWidth: true
            text: "The preferred agent is used for new plans. Coding executors and timeouts are reviewed per plan step. CLI authentication and configuration stay with the installed tools."
            color: Theme.muted
        }
        CedarText {
            text: "SKILLS"
            color: Theme.accent
            font.letterSpacing: 2
        }
        CedarText {
            Layout.fillWidth: true
            text: "Discovery directories\n" + (BrainStore.skillInfo.skill_dirs || []).join("\n") + "\n\nImports are limited by the backend's configured import roots. Trust preferences and remote installation are not configurable in v1."
            color: Theme.muted
        }
        CedarText {
            text: "EXECUTION"
            color: Theme.accent
            font.letterSpacing: 2
        }
        CedarText {
            Layout.fillWidth: true
            text: "Explicit approval is required for every run. Write steps need separate approval.\nMaximum parallel steps: " + (BrainStore.capabilities.limits?.max_parallel_steps ?? "unknown") + "\nMaximum agent processes: " + (BrainStore.capabilities.limits?.max_agent_processes ?? "unknown") + "\nDefault step timeout: " + (BrainStore.capabilities.limits?.default_step_timeout_s ?? "unknown") + " s\nPer-step timeout range: 10–7200 s.\n\nBackend settings are read-only through protocol v1. Edit ~/.config/cedar-brain/config.json outside this application to change service defaults."
            color: Theme.muted
        }
        CedarText {
            text: "DIAGNOSTICS"
            color: Theme.accent
            font.letterSpacing: 2
        }
        CedarText {
            Layout.fillWidth: true
            text: "Mycelium frontend 0.1.0 · protocol 1\nBackend " + (BrainStore.health.version || "unknown") + " · " + BrainConnection.state + "\nSocket: " + BrainConnection.socketPath + "\nState: " + (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/cedar-brain\nCache base: " + (Quickshell.env("XDG_CACHE_HOME") || Quickshell.env("HOME") + "/.cache") + "\nStorage: " + (BrainStore.health.database?.ok ? "database healthy" : "unknown or degraded") + "\nGraph: " + (BrainStore.graphStatus.state || "not loaded")
            color: Theme.muted
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            CedarButton {
                text: "Refresh health"
                enabled: BrainConnection.ready
                onClicked: BrainStore.refresh()
            }
            CedarButton {
                text: "Inspect diagnostics"
                onClicked: NavigationState.inspect("Diagnostics", Models.pretty({
                    health: BrainStore.health,
                    project: BrainStore.projectStatus,
                    graph: BrainStore.graphStatus,
                    skillProblems: BrainStore.skillInfo.problems,
                    capabilities: BrainStore.capabilities
                }), "diagnostics", "")
            }
        }
        Item {
            implicitHeight: 20
        }
    }
}

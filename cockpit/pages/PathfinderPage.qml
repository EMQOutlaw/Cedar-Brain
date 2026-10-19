pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components"
import "../core/Models.js" as Models

ScrollView {
    id: page
    property bool revise: false
    readonly property bool composing: !BrainStore.plan || BrainStore.plan.status !== "ready" || revise
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        width: page.availableWidth
        spacing: 24
        CedarHeader {
            Layout.fillWidth: true
            title: "What are we building?"
            subtitle: "Plan work with your installed agents and explore the project before you act."
        }
        CedarSurface {
            Layout.fillWidth: true
            implicitHeight: promptLayout.implicitHeight + 40
            topLine: true
            topLineColor: Theme.accent
            ColumnLayout {
                id: promptLayout
                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: 20
                }
                spacing: 14
                CedarText {
                    text: "01  /  SET A DIRECTION"
                    font.pixelSize: 12
                    color: Theme.accent
                    font.letterSpacing: 2
                }
                ScrollView {
                    visible: page.composing
                    Layout.fillWidth: true
                    Layout.preferredHeight: 150
                    clip: true
                    TextArea {
                        id: prompt
                        text: BrainStore.objective
                        placeholderText: "Describe a change, trace a dependency, or investigate a problem…"
                        color: Theme.text
                        placeholderTextColor: Theme.muted
                        selectionColor: Theme.border
                        wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.PlainText
                        font.family: Theme.labelFont
                        font.pixelSize: 22
                        background: Rectangle {
                            color: Theme.background
                            border.width: prompt.activeFocus ? 1 : 0
                            border.color: Theme.accent
                        }
                        Accessible.name: "Task objective"
                        onTextChanged: BrainStore.objective = text
                    }
                }
                CedarText {
                    visible: !page.composing
                    text: BrainStore.plan?.objective || ""
                    Layout.fillWidth: true
                    font.pixelSize: 20
                }
                CedarButton {
                    visible: !page.composing
                    text: "Revise request"
                    onClicked: page.revise = true
                }
                Flow {
                    visible: page.composing
                    Layout.fillWidth: true
                    spacing: 10
                    CedarCombo {
                        model: ["claude", "codex"]
                        currentIndex: Theme.preferredAgent === "codex" ? 1 : 0
                        onActivated: index => Theme.preferredAgent = model[index]
                        Accessible.name: "Planning agent"
                    }
                    CedarButton {
                        text: BrainStore.planning ? "Generating plan…" : "Create plan"
                        primary: true
                        enabled: BrainStore.supports("pathfinder.plans") && !!BrainStore.projectId && !BrainStore.planning && prompt.text.trim().length > 0 && prompt.text.length <= 4000
                        onClicked: {
                            page.revise = false;
                            BrainStore.createPlan();
                        }
                    }
                    CedarButton {
                        text: "Clear"
                        onClicked: BrainStore.objective = ""
                    }
                    CedarButton {
                        text: "Browse skills"
                        onClicked: NavigationState.page = 2
                    }
                }
                CedarText {
                    Layout.fillWidth: true
                    text: "Planning is read-only. You review and approve before execution. Skills can be inspected; v1 cannot attach or execute them."
                    color: Theme.muted
                    font.pixelSize: 13
                }
                CedarText {
                    visible: prompt.text.length > 4000
                    text: "Requests must be at most 4,000 characters."
                    color: Theme.amber
                }
            }
        }
        ColumnLayout {
            visible: !!BrainStore.plan
            Layout.fillWidth: true
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                CedarText {
                    text: "02  /  REVIEW THE PLAN"
                    color: Theme.accent
                    font.letterSpacing: 2
                    Layout.fillWidth: true
                }
                CedarStatus {
                    status: BrainStore.plan?.status || ""
                }
            }
            CedarText {
                Layout.fillWidth: true
                text: BrainStore.definition?.name || BrainStore.definition?.title || BrainStore.plan?.objective || ""
                font.pixelSize: 24
            }
            CedarText {
                Layout.fillWidth: true
                text: BrainStore.definition?.summary || ""
                color: Theme.muted
            }
            Repeater {
                model: Array.isArray(BrainStore.definition?.steps) ? BrainStore.definition.steps : []
                CedarSurface {
                    id:planStep
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: stepContent.implicitHeight + 32
                    ColumnLayout {
                        id: stepContent
                        anchors {
                            left: parent.left
                            right: parent.right
                            top: parent.top
                            margins: 16
                        }
                        CedarText {
                            Layout.fillWidth: true
                            text: (planStep.index + 1) + "  " + planStep.modelData.description
                            font.pixelSize: 20
                        }
                        CedarText {
                            Layout.fillWidth: true
                            text: planStep.modelData.executor + " · " + (planStep.modelData.mode || "read_only") + " · " + (planStep.modelData.approval_required ? "Step approval required" : "Covered by run approval")
                            color: planStep.modelData.approval_required ? Theme.amber : Theme.accent
                        }
                        CedarText {
                            Layout.fillWidth: true
                            text: "Depends on: " + ((planStep.modelData.depends_on || []).join(", ") || "none") + "\nExpected: " + planStep.modelData.expected_output + "\nValidation: " + planStep.modelData.validation
                            color: Theme.muted
                        }
                        CedarText {
                            visible: planStep.modelData.mode === "write"
                            Layout.fillWidth: true
                            text: "Modifies files in an isolated worktree. Changes are never automatically merged."
                            color: Theme.amber
                        }
                    }
                }
            }
            CedarText {
                Layout.fillWidth: true
                visible: !!BrainStore.plan?.error || BrainStore.validation?.valid === false || BrainStore.validation?.executable === false
                text: Models.pretty(BrainStore.plan?.error || BrainStore.validation)
                color: Theme.amber
            }
            Flow {
                Layout.fillWidth: true
                spacing: 10
                CedarButton {
                    text: "Edit definition"
                    enabled: !!BrainStore.definition
                    onClicked: {
                        editor.text = JSON.stringify(Models.normalDefinition(BrainStore.definition), null, 2);
                        editorSurface.visible = !editorSurface.visible;
                    }
                }
                CedarButton {
                    text: "Validate changes"
                    visible: BrainStore.edited
                    enabled: BrainConnection.ready
                    onClicked: BrainStore.validatePlan()
                }
                CedarButton {
                    text: BrainStore.starting ? "Starting…" : "Approve and start"
                    primary: true
                    enabled: BrainStore.supports("hyphae.workflows") && !!BrainStore.validation?.valid && !!BrainStore.validation?.executable && !BrainStore.starting && !editorSurface.visible
                    onClicked: BrainStore.approvePlan()
                }
            }
            CedarSurface {
                id: editorSurface
                visible: false
                Layout.fillWidth: true
                implicitHeight: 350
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    CedarText {
                        Layout.fillWidth: true
                        text: "Advanced plan editor · reorder the steps array or edit fields, then apply and validate."
                        color: Theme.muted
                    }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        TextArea {
                            id: editor
                            textFormat: TextEdit.PlainText
                            color: Theme.text
                            font.family: Theme.dataFont
                            wrapMode: TextEdit.Wrap
                            selectByMouse: true
                            background: null
                        }
                    }
                    CedarButton {
                        text: "Apply draft"
                        onClicked: {
                            BrainStore.editDefinition(editor.text);
                            editorSurface.visible = false;
                        }
                    }
                }
            }
        }
        CedarText {
            visible: BrainStore.recentRequests.length > 0
            text: "RECENT REQUESTS"
            color: Theme.muted
            font.letterSpacing: 2
            font.pixelSize: 12
        }
        Repeater {
            model: BrainStore.recentRequests
            CedarButton {
                required property string modelData
                text: modelData
                Layout.fillWidth: true
                onClicked: BrainStore.objective = modelData
            }
        }
        Item {
            implicitHeight: 16
        }
    }
}

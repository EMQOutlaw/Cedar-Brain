pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components"

ColumnLayout {
    id: page
    spacing: 16
    CedarHeader {
        Layout.fillWidth: true
        title: "Spore Library"
        subtitle: "Local knowledge, with its origins in view."
    }
    RowLayout {
        Layout.fillWidth: true
        CedarInput {
            id: search
            Layout.fillWidth: true
            placeholderText: "Filter local skills…"
        }
        CedarCombo {
            id: filter
            model: ["All skills", "Compatible", "Needs attention", "Imported"]
            Accessible.name: "Skill filter"
        }
        CedarButton {
            text: "Rescan"
            enabled: BrainStore.supports("skills.local_discovery")
            onClicked: BrainStore.refreshSkills(true)
        }
    }
    CedarText {
        Layout.fillWidth: true
        text: "Local metadata does not establish trust. Skills are inspected, never executed, in protocol v1."
        color: Theme.muted
    }
    ListView {
        id: list
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 8
        model: BrainStore.skills.filter(s => ((s.name || "") + " " + (s.description || "")).toLowerCase().includes(search.text.toLowerCase()) && (filter.currentIndex === 0 || filter.currentIndex === 1 && s.compatibility === "compatible" || filter.currentIndex === 2 && s.compatibility !== "compatible" || filter.currentIndex === 3 && s.imported))
        ScrollBar.vertical: ScrollBar {}
        delegate: CedarSurface {
            id: skillCard
            required property var modelData
            width: list.width
            implicitHeight: skillRow.implicitHeight + 28
            ColumnLayout {
                id: skillRow
                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: 14
                }
                RowLayout {
                    Layout.fillWidth: true
                    CedarText {
                        text: skillCard.modelData.name || "Invalid skill"
                        Layout.fillWidth: true
                        font.pixelSize: 22
                    }
                    CedarStatus {
                        status: skillCard.modelData.trust || "untrusted"
                    }
                    CedarButton {
                        text: "Inspect"
                        enabled: BrainConnection.ready
                        onClicked: BrainStore.inspectSkill(skillCard.modelData.id)
                    }
                }
                CedarText {
                    text: skillCard.modelData.description || "No description declared"
                    Layout.fillWidth: true
                    color: Theme.muted
                    maximumLineCount: 3
                    elide: Text.ElideRight
                }
                CedarText {
                    text: (skillCard.modelData.provenance?.kind || "local") + " · " + (skillCard.modelData.compatibility || "invalid") + " · " + (skillCard.modelData.imported ? "Imported" : "Discovered locally") + " · " + (skillCard.modelData.version || "No version")
                    Layout.fillWidth: true
                    color: Theme.accent
                    font.pixelSize: 13
                }
            }
        }
        CedarText {
            anchors.centerIn: parent
            visible: list.count === 0
            text: "No matching local skills."
            color: Theme.muted
        }
    }
    CedarText {
        visible: (BrainStore.skillInfo.problems || []).length > 0
        Layout.fillWidth: true
        text: (BrainStore.skillInfo.problems || []).length + " discovery problems. Open diagnostics to inspect paths and details."
        color: Theme.amber
    }
    RowLayout {
        Layout.fillWidth: true
        CedarInput {
            id: importPath
            Layout.fillWidth: true
            placeholderText: "Local directory containing SKILL.md"
        }
        CedarButton {
            text: "Import local"
            enabled: BrainStore.supports("skills.local_import") && importPath.text.trim() !== ""
            onClicked: BrainStore.importSkill(importPath.text)
        }
    }
}

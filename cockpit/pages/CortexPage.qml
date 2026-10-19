import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components"
import "../graph"

ScrollView {
    id: page
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        width: page.availableWidth
        height: Math.max(page.availableHeight, page.availableWidth < 700 ? 800 : 650)
        spacing: 12
        CedarHeader {
            Layout.fillWidth: true
            title: "Cortex"
            subtitle: "Read the architecture. Follow the relationships."
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            CedarInput {
                id: query
                width: 230
                placeholderText: "Search names, paths, types…"
                maximumLength: 200
                onAccepted: BrainStore.queryGraph(text)
            }
            CedarButton {
                text: "Search"
                enabled: BrainConnection.ready && !!BrainStore.graphStatus.loaded
                onClicked: BrainStore.queryGraph(query.text)
            }
            CedarCombo {
                model: BrainStore.communities
                textRole: "name"
                displayText: currentIndex >= 0 ? currentText : "Community"
                onActivated: index => BrainStore.communityGraph(BrainStore.communities[index].id)
                enabled: BrainConnection.ready && !!BrainStore.graphStatus.loaded
                Accessible.name: "Community filter"
            }
            CedarButton {
                text: "Reload graph"
                enabled: BrainConnection.ready && !!BrainStore.projectId
                onClicked: BrainStore.call("cortex.reload", {
                    project_id: BrainStore.projectId
                }, () => BrainStore.refreshProject())
            }
        }
        CedarText {
            Layout.fillWidth: true
            color: Theme.muted
            font.pixelSize: 13
            text: BrainStore.graphStatus.loaded ? BrainStore.graphStatus.nodes + " nodes · " + BrainStore.graphStatus.edges + " edges indexed · freshness: " + (BrainStore.graphStatus.freshness?.state || "unknown") + (BrainStore.graphStatus.freshness?.changed_on_disk ? " · Changed on disk; reload available" : "") : "No graph loaded. Register a Graphify graph in Settings to explore this project."
        }
        CedarSurface {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180
            GraphCanvas {
                id: graph
                objectName: "cortexGraph"
                anchors.fill: parent
                anchors.margins: 1
                nodes: BrainStore.graphNodes
                edges: BrainStore.graphEdges
                rendering: page.visible
                selectedId: BrainStore.nodeDetail?.node?.id || ""
                onSelected: id => BrainStore.inspectNode(id)
            }
            CedarText {
                anchors.centerIn: parent
                width: parent.width * .7
                horizontalAlignment: Text.AlignHCenter
                visible: BrainStore.graphNodes.length === 0
                text: BrainStore.graphStatus.loaded ? "Choose a community or search the graph.\nOnly queried nodes enter the workspace." : "The graph is quiet.\nNo project architecture is available yet."
                color: Theme.muted
                font.pixelSize: 22
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            CedarButton {
                text: "Reset view"
                onClicked: graph.resetView()
            }
            CedarButton {
                text: "Center selection"
                enabled: !!BrainStore.nodeDetail
                onClicked: graph.centerSelection()
            }
            CedarCombo {
                id: relationship
                width: 140
                model: ["All relations"].concat(Object.keys(BrainStore.graphStatus.relations || {}))
                Accessible.name: "Relationship filter"
            }
            CedarButton {
                text: "Show dependencies"
                enabled: BrainConnection.ready && !!BrainStore.nodeDetail
                onClicked: BrainStore.dependencies("both", relationship.currentIndex > 0 ? relationship.currentText : "")
            }
        }
        CedarText {
            Layout.fillWidth: true
            text: (BrainStore.graphTruncated ? "BOUNDED RESULT · More nodes exist. Refine the query.\n" : "") + BrainStore.graphNote
            color: BrainStore.graphTruncated ? Theme.amber : Theme.muted
            font.pixelSize: 12
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        ListView {
            Layout.fillWidth: true
            Layout.preferredHeight: 100
            clip: true
            model: BrainStore.graphNodes
            ScrollBar.vertical: ScrollBar {}
            delegate: CedarButton {
                required property var modelData
                width: ListView.view.width
                height: 34
                enabled: BrainConnection.ready
                text: modelData.label + "  ·  " + modelData.source_file
                onClicked: BrainStore.inspectNode(modelData.id)
            }
        }
    }
}

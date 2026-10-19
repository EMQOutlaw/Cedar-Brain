pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import "core"
import "components"

FloatingWindow {
    id: window
    title: "CEDAR Brain — Mycelium"
    implicitWidth: 1280
    implicitHeight: 820
    minimumSize: Qt.size(720, 560)
    color: Theme.background
    visible: true
    onClosed: Qt.quit()
    property alias canvas: frame
    Rectangle {
        id: frame
        color: Theme.background
        anchors.fill: parent
        readonly property bool wide: width >= 1000
        readonly property bool sideInspector: width >= 1180
        Shortcut {
            sequence: "Ctrl+1"
            onActivated: NavigationState.page = 0
        }
        Shortcut {
            sequence: "Ctrl+2"
            onActivated: NavigationState.page = 1
        }
        Shortcut {
            sequence: "Ctrl+3"
            onActivated: NavigationState.page = 2
        }
        Shortcut {
            sequence: "Ctrl+4"
            onActivated: NavigationState.page = 3
        }
        Shortcut {
            sequence: "Ctrl+5"
            onActivated: NavigationState.page = 4
        }
        Shortcut {
            sequence: "Ctrl+6"
            onActivated: NavigationState.page = 5
        }
        Shortcut {
            sequence: "Escape"
            onActivated: NavigationState.inspectorOpen = false
        }
        Rectangle {
            id: rail
            width: frame.wide ? 210 : 70
            height: parent.height
            color: Theme.surface
            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Qt.alpha(Theme.accent, .2)
            }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: frame.wide ? 20 : 8
                spacing: 12
                RowLayout {
                    CedarMark {
                        Layout.preferredWidth: 32
                        Layout.preferredHeight: 42
                    }
                    CedarText {
                        visible: frame.wide
                        text: "CEDAR\nBRAIN"
                        font.pixelSize: 22
                        font.letterSpacing: 3
                        lineHeight: .85
                    }
                }
                CedarText {
                    visible: frame.wide
                    text: "M Y C E L I U M"
                    font.pixelSize: 11
                    color: Theme.accent
                    Layout.topMargin: 4
                    Layout.bottomMargin: 24
                }
                Repeater {
                    model: NavigationState.names
                    CedarButton {
                        id: navButton
                        required property string modelData
                        required property int index
                        objectName: "nav-" + index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        text: frame.wide ? ("0" + (index + 1) + "   " + modelData) : ("0" + (index + 1))
                        Accessible.name: navButton.modelData
                        checked: NavigationState.page === index
                        ToolTip.visible: hovered && !frame.wide
                        ToolTip.text: navButton.modelData
                        onClicked: NavigationState.page = navButton.index
                    }
                }
                Item {
                    Layout.fillHeight: true
                }
                CedarText {
                    visible: frame.wide
                    text: BrainConnection.fixtureMode ? "FIXTURE SESSION\nRecorded backend data" : "LOCAL WORKSPACE\nProtocol v1"
                    color: Theme.muted
                    font.pixelSize: 12
                    Layout.fillWidth: true
                }
                CedarStatus {
                    status: BrainConnection.state
                    Layout.fillWidth: true
                    visible: frame.wide
                }
                Rectangle {
                    visible: !frame.wide
                    implicitWidth: 8
                    implicitHeight: 8
                    radius: 4
                    color: Theme.statusColor(BrainConnection.state)
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }
        ColumnLayout {
            id: body
            anchors {
                left: rail.right
                right: parent.right
                top: parent.top
                bottom: parent.bottom
                margins: 24
            }
            spacing: 16
            RowLayout {
                Layout.fillWidth: true
                CedarText {
                    text: "PROJECT"
                    color: Theme.muted
                    font.pixelSize: 12
                    font.letterSpacing: 2
                }
                CedarCombo {
                    Layout.fillWidth: true
                    Layout.maximumWidth: 360
                    model: BrainStore.projects
                    textRole: "name"
                    currentIndex: BrainStore.projects.findIndex(p => p.id === BrainStore.projectId)
                    displayText: BrainStore.project?.name || "Select a project"
                    enabled: BrainConnection.ready
                    onActivated: index => BrainStore.selectProject(BrainStore.projects[index].id)
                }
                CedarButton {
                    text: "Add project"
                    enabled: BrainStore.supports("projects.registry")
                    onClicked: {
                        NavigationState.page = 5;
                    }
                }
                Item {
                    Layout.fillWidth: true
                }
                CedarText {
                    visible: frame.wide
                    text: BrainStore.projectStatus.git?.branch || "MYCELIUM"
                    color: Theme.muted
                    font.family: Theme.dataFont
                    font.pixelSize: 12
                }
            }
            CedarSurface {
                visible: !BrainConnection.ready || BrainConnection.fixtureMode
                Layout.fillWidth: true
                implicitHeight: connectionText.implicitHeight + 24
                CedarText {
                    id: connectionText
                    anchors.fill: parent
                    anchors.margins: 12
                    color: Theme.amber
                    text: BrainConnection.fixtureMode ? "FIXTURE DATA · Recorded responses; actions do not represent live work." + (BrainConnection.ready ? "" : " Backend " + BrainConnection.state + "; last known state.") : "Backend " + BrainConnection.state + ". " + (BrainConnection.errorText || "Connecting to the local service…") + " Last known state is not live."
                }
            }
            RowLayout {
                visible: BrainStore.errorText !== ""
                Layout.fillWidth: true
                CedarButton {
                    text: "Inspect error"
                    onClicked: NavigationState.inspect("Backend error", BrainStore.errorText, "error", "")
                }
                CedarText {
                    text: BrainStore.errorText
                    color: Theme.ember
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                CedarButton {
                    text: "Dismiss"
                    onClicked: BrainStore.errorText = ""
                }
            }
            Item {
                id: workspace
                Layout.fillWidth: true
                Layout.fillHeight: true
                Item {
                    id: pageHost
                    anchors {
                        left: parent.left
                        top: parent.top
                        bottom: parent.bottom
                    }
                    width: parent.width - (frame.sideInspector && NavigationState.inspectorOpen ? Math.min(400, parent.width * .39) + 16 : 0)
                    enabled: frame.sideInspector || !NavigationState.inspectorOpen
                    // Retain each visited page once. Hidden pages stop graph painting.
                    Repeater {
                        model: ["PathfinderPage", "CortexPage", "SporeLibraryPage", "HyphaePage", "AgentGrovePage", "SettingsPage"]
                        Loader {
                            required property string modelData
                            required property int index
                            property bool visited: false
                            anchors.fill: parent
                            active: visited || NavigationState.page === index
                            visible: NavigationState.page === index
                            source: "pages/" + modelData + ".qml"
                            onVisibleChanged: if (visible)
                                visited = true
                        }
                    }
                }
                CedarInspector {
                    anchors {
                        right: parent.right
                        top: parent.top
                        bottom: parent.bottom
                    }
                    width: frame.sideInspector ? Math.min(400, parent.width * .39) : parent.width
                    // The background unfolds inside the allocated workspace; hitboxes stay fixed.
                    scale: 1
                }
            }
        }
    }
}

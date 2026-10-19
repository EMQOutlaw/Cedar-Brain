import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"

Item {
    id: root
    property real reveal: NavigationState.inspectorOpen ? 1 : 0
    clip: true
    visible: reveal > 0
    enabled: reveal > .99 && NavigationState.inspectorOpen
    onEnabledChanged: if (enabled)
        Qt.callLater(() => closeButton.forceActiveFocus())
    Behavior on reveal {
        NumberAnimation {
            duration: NavigationState.inspectorOpen ? Theme.morphGrow : Theme.morphShrink
            easing.type: Easing.OutCubic
        }
    }
    CedarSurface {
        width: parent.width
        height: parent.height * root.reveal
        topLine: true
        topLineColor: Theme.accent
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        opacity: Math.max(0, (root.reveal - .5) * 2)
        RowLayout {
            Layout.fillWidth: true
            CedarText {
                text: NavigationState.inspectorTitle
                font.pixelSize: 22
                Layout.fillWidth: true
            }
            CedarButton {
                id: closeButton
                text: "Close"
                onClicked: NavigationState.inspectorOpen = false
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 6
            visible: NavigationState.inspectorKind === "node"
            CedarButton {
                text: "Dependencies"
                enabled: BrainConnection.ready
                onClicked: BrainStore.dependencies("out")
            }
            CedarButton {
                text: "Dependents"
                enabled: BrainConnection.ready
                onClicked: BrainStore.dependencies("in")
            }
            CedarButton {
                text: "Impact candidates"
                enabled: BrainConnection.ready
                onClicked: BrainStore.impact()
            }
        }
        CedarButton {
            visible: NavigationState.inspectorKind === "agent"
            text: "Prefer for new plans"
            onClicked: Theme.preferredAgent = NavigationState.selection
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            TextArea {
                text: NavigationState.inspectorText
                readOnly: true
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                color: Theme.text
                font.family: Theme.dataFont
                font.pixelSize: 12
                selectByMouse: true
                background: null
            }
        }
    }
}

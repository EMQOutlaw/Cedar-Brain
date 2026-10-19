import QtQuick
import Quickshell
import "."
import "core"

ShellRoot {
    id: test
    property int phase: 0
    property int connections: 0
    property int mutations: 0
    property int subscriptions: 0
    property int events: 0
    property bool sawDisconnect: false
    AppWindow {}
    Connections {
        target: BrainConnection
        function onEstablished(restarted) {
            test.connections++;
        }
        function onStateChanged() {
            if (test.connections > 0 && BrainConnection.state === "disconnected")
                test.sawDisconnect = true;
        }
        function onEventReceived(name, data) {
            test.events++;
        }
        function onRequestSent(method, params) {
            if (method === "events.subscribe")
                test.subscriptions++;
            if (["workflows.create", "runs.start", "runs.cancel", "runs.approve"].includes(method))
                test.mutations++;
        }
    }
    Timer {
        interval: 300
        repeat: true
        running: true
        onTriggered: {
            if (test.phase === 0 && BrainConnection.ready && BrainStore.projectId && BrainStore.supports("pathfinder.plans")) {
                if (test.mutations !== 0) {
                    console.error("SOCKET_FAIL startup mutation");
                    Qt.quit();
                    return;
                }
                BrainStore.objective = "Fixture socket plan";
                BrainStore.createPlan();
                test.phase++;
            } else if (test.phase === 1 && BrainStore.plan?.status === "ready") {
                if (test.mutations !== 0) {
                    console.error("SOCKET_FAIL automatic execution");
                    Qt.quit();
                    return;
                }
                BrainStore.approvePlan();
                test.phase++;
            } else if (test.phase === 2 && BrainStore.run?.steps?.length > 0) {
                NavigationState.page = 1;
                BrainStore.queryGraph("strata");
                test.phase++;
            } else if (test.phase === 3 && BrainStore.graphNodes.length > 0 && test.events > 0) {
                console.log("SOCKET_RECONNECT_REQUEST");
                test.phase++;
            } else if (test.phase === 4 && test.connections >= 2 && test.sawDisconnect) {
                if (test.mutations !== 2 || test.subscriptions < 2)
                    console.error("SOCKET_FAIL reconnect mutation or subscription");
                else
                    console.log("SOCKET_PASS connections=" + test.connections + " subscriptions=" + test.subscriptions + " events=" + test.events);
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 30000
        running: true
        onTriggered: {
            console.error("SOCKET_FAIL timeout phase=" + test.phase + " error=" + BrainStore.errorText);
            Qt.quit();
        }
    }
}

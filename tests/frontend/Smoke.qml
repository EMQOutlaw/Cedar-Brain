import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import "."
import "core"

ShellRoot {
    id: test
    property var fixtures: ({})
    property int phase: 0
    property int writes: 0
    property int requests: 0
    property var sent: []
    property var responseQueue: []
    property var stages: []
    property bool capturing: false
    property double denseStarted: 0
    property var denseGeometry: null
    AppWindow {
        id: app
        TestCase {
            id: input
            name: "MyceliumInput"
            when: false
        }
    }
    FileView {
        path: Quickshell.env("MYCELIUM_FIXTURE_FILE")
        onLoaded: {
            test.fixtures = JSON.parse(text());
            BrainConnection.testTransport = wire => {
                const req = JSON.parse(wire);
                test.requests++;
                test.sent.push(req);
                if (["runs.start", "workflows.create", "runs.cancel", "runs.approve"].includes(req.method))
                    test.writes++;
                const result = test.fixtures[req.method];
                test.responseQueue.push(JSON.stringify({
                    v: 1,
                    id: req.id,
                    ok: !!result,
                    result: result,
                    error: {
                        code: "NOT_FOUND",
                        message: "No fixture"
                    }
                }));
                responses.start();
            };
            BrainConnection.state = "connected";
            BrainConnection.established(false);
            test.stages = [() => {
                    input.mouseClick(test.find(app.canvas, "nav-4"));
                }, () => {
                    test.check(NavigationState.page === 4, "mouse navigation");
                    const nav = test.find(app.canvas, "nav-0");
                    nav.forceActiveFocus();
                    input.keyClick(Qt.Key_Space);
                }, () => {
                    test.check(NavigationState.page === 0, "keyboard navigation");
                }, () => {
                    test.check(BrainStore.projects.length > 0, "project fixtures");
                    test.check(BrainStore.agents.length > 0, "agent fixtures");
                    test.check(test.writes === 0, "no execution on startup");
                    BrainStore.objective = "Inspect this project";
                    BrainStore.createPlan();
                }, () => {
                    test.check(BrainStore.plan?.status === "ready", "plan ready, including early completion reconciliation");
                    test.check(test.writes === 0, "planning never executes");
                    test.capture("pathfinder");
                }, () => {
                    NavigationState.page = 1;
                    BrainStore.queryGraph("strata");
                }, () => {
                    test.check(BrainStore.graphNodes.length > 0, "graph search");
                    BrainStore.inspectNode(BrainStore.graphNodes[0].id);
                }, () => {
                    BrainStore.dependencies("both");
                }, () => {
                    test.check(BrainStore.graphEdges.length > 0, "graph relationships");
                    test.capture("cortex");
                }, () => {
                    NavigationState.page = 2;
                    if (BrainStore.skills.length)
                        BrainStore.inspectSkill(BrainStore.skills[0].id);
                }, () => test.capture("skills"), () => {
                    NavigationState.page = 4;
                    BrainStore.inspectAgent("codex");
                }, () => test.capture("agents"), () => {
                    NavigationState.page = 5;
                }, () => test.capture("settings"), () => {
                    NavigationState.page = 0;
                    BrainStore.approvePlan();
                }, () => {
                    test.check(test.writes === 2, "only explicit approval creates and starts run");
                    test.check(!!BrainStore.run, "run loaded");
                    BrainStore.inspectStep(BrainStore.run.steps[0].step_id);
                }, () => {
                    test.capture("hyphae");
                    test.check(BrainStore.logs.length <= 400, "bounded replay");
                }, () => {
                    const step=BrainStore.run.steps[0].step_id;
                    BrainStore.approveStep(step,false);
                    const request=test.sent[test.sent.length-1];
                    test.check(request.method==="runs.approve" && request.params.step_id===step && request.params.approve===false,"step rejection uses documented API");
                }, () => {
                    BrainStore.run=Object.assign({},BrainStore.run,{status:"running"});
                    // The published mock returns fixed IDs. Normalize this recorded
                    // cancellation result to the selected run for this state test.
                    test.fixtures["runs.cancel"]=Object.assign({},test.fixtures["runs.cancel"],{id:BrainStore.run.id});
                    BrainStore.cancelRun();
                    test.check(BrainStore.cancelling && BrainStore.run.status==="running","cancellation request is distinct from confirmation");
                    test.check(test.sent[test.sent.length-1].method==="runs.cancel","cancellation uses documented API");
                }, () => {
                    test.check(!BrainStore.cancelling && BrainStore.run.status==="cancelled","cancellation confirmed only by response");
                }, () => {
                    for (let i = 1; i <= 5000; i++)
                        BrainConnection.eventReceived("run.output", {
                            run_id: BrainStore.run.id,
                            seq: i,
                            step_id: BrainStore.run.steps[0].step_id,
                            kind: "message",
                            text: "Long output <b>plain text</b> " + i
                        });
                }, () => {
                    test.check(BrainStore.logs.length <= 400, "bounded 5000-message burst");
                    test.check(BrainStore.logQueue.length === 0, "batch drained");
                    test.resize(720, 560);
                    NavigationState.page = 1;
                    BrainStore.inspectNode(BrainStore.graphNodes[0].id);
                }, () => {
                    test.check(app.canvas.width === 720 && app.canvas.height === 560, "native window resized to 720x560");
                    test.capture("compact-inspector");
                }, () => {
                    NavigationState.inspectorOpen = false;
                }, () => test.capture("compact-cortex"), () => {
                    for (let i = 0; i < 120; i++)
                        NavigationState.page = i % 6;
                    Theme.reducedMotion = true;
                    test.resize(1920, 800);
                    NavigationState.page = 1;
                }, () => {
                    test.check(app.canvas.width === 1920, "native ultrawide resize");
                    test.capture("ultrawide");
                }, () => {
                    const nodes = [];
                    for (let i = 0; i < 5000; i++)
                        nodes.push({
                            id: "n" + i,
                            label: "Synthetic load node " + i,
                            community: 0,
                            source_file: "test.qml"
                        });
                    test.denseStarted = Date.now();
                    BrainStore.graphNodes = nodes;
                    BrainStore.graphEdges = [];
                }, () => {
                    const graph = test.find(app.canvas, "cortexGraph");
                    test.check(!!graph, "graph instantiated");
                    test.check(graph.paintedNodes > 0 && graph.paintedNodes < 500, "viewport culls 5000-node synthetic graph");
                    test.denseGeometry = graph.geometry;
                    console.log("DENSITY painted=" + graph.paintedNodes + " total=5000");
                    graph.panX -= 200;
                    graph.redraw();
                }, () => {
                    const graph = test.find(app.canvas, "cortexGraph");
                    test.check(graph.geometry === test.denseGeometry, "pan preserves cached geometry");
                    NavigationState.page = 0;
                }, () => {
                    const graph = test.find(app.canvas, "cortexGraph");
                    test.check(!graph.rendering, "hidden graph rendering disabled");
                    BrainConnection.state = "disconnected";
                }, () => test.capture("disconnected"), () => {
                    const before = test.writes;
                    BrainConnection.state = "connected";
                    BrainConnection.established(true);
                    test.check(test.writes === before, "reconnect never replays mutations");
                }, () => {
                    BrainStore.projects = [];
                    BrainStore.projectId = "";
                    BrainStore.skills = [
                        {
                            id: "bad",
                            name: "<b>Untrusted</b>",
                            description: null,
                            compatibility: "invalid",
                            trust: "untrusted"
                        }
                    ];
                    NavigationState.page = 2;
                }, () => {
                    test.capture("invalid-skill");
                    test.check(!BrainStore.plan && !BrainStore.run, "project switch clears scoped state");
                }, () => {
                    BrainConnection.pending["test-one"] = {
                        callback: (r, e) => test.check(r.answer === 1, "response correlation first"),
                        method: "test",
                        deadline: Date.now() + 1000
                    };
                    BrainConnection.pending["test-two"] = {
                        callback: (r, e) => test.check(r.answer === 2, "response correlation second"),
                        method: "test",
                        deadline: Date.now() + 1000
                    };
                    BrainConnection.receive('{"v":1,"id":"test-two","ok":true,"result":{"answer":2}}');
                    BrainConnection.receive('{"v":1,"id":"test-one","ok":true,"result":{"answer":1}}');
                    BrainConnection.receive('{"v":2,"id":"other","ok":true,"result":{}}');
                    test.check(BrainConnection.state === "incompatible protocol", "incompatible version");
                }, () => {
                    console.log("FRONTEND_PASS requests=" + test.requests);
                    Qt.quit();
                }];
            advance.start();
        }
    }
    function resize(w, h) {
        app.canvas.Window.window.width = w;
        app.canvas.Window.window.height = h;
    }
    function find(item, name) {
        if (item.objectName === name)
            return item;
        for (const child of item.children || []) {
            const result = find(child, name);
            if (result)
                return result;
        }
        return null;
    }
    function check(ok, message) {
        if (!ok) {
            console.error("FRONTEND_FAIL " + message);
            advance.stop();
            Qt.quit();
        } else
            console.log("PASS " + message);
    }
    function capture(name) {
        const dir = Quickshell.env("MYCELIUM_EVIDENCE");
        if (dir) {
            capturing = true;
            app.canvas.grabToImage(r => {
                r.saveToFile(dir + "/" + name + ".png");
                capturing = false;
                console.log("CAPTURE " + name + " " + app.canvas.width + "x" + app.canvas.height);
            });
        }
    }
    Timer {
        id: responses
        interval: 10
        onTriggered: {
            const responses = test.responseQueue.splice(0).reverse();
            for (const line of responses)
                BrainConnection.receive(line);
            if (test.responseQueue.length)
                start();
        }
    }
    Timer {
        id: advance
        interval: 450
        repeat: true
        onTriggered: {
            if (!test.capturing && test.phase < test.stages.length)
                test.stages[test.phase++]();
        }
    }
    Timer {
        running: true
        interval: 25000
        onTriggered: {
            console.error("FRONTEND_FAIL timeout");
            Qt.quit();
        }
    }
}

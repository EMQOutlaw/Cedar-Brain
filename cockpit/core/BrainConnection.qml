pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Singleton {
    id: root
    readonly property string socketPath: {
        const override = Quickshell.env("CEDAR_BRAIN_SOCKET");
        if (override && override.startsWith("/"))
            return override;
        const runtime = Quickshell.env("XDG_RUNTIME_DIR");
        return runtime ? runtime + "/cedar-brain/brain.sock" : (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/cedar-brain/run/brain.sock";
    }
    readonly property bool fixtureMode: Quickshell.env("CEDAR_BRAIN_FIXTURES") === "1"
    property string state: "connecting"
    property string errorText: ""
    property string instanceId: ""
    readonly property bool ready: state === "connected"
    // Dependency injection for the offline contract harness; production uses Socket.
    property var testTransport: null
    property int pendingCount: 0
    property var pending: ({})
    property int serial: 0
    property int backoff: 250
    signal established(bool restarted)
    signal eventReceived(string name, var data)
    signal failed(string message)
    signal requestSent(string method, var params)
    function request(method, params, callback) {
        if (!socket.connected && !testTransport) {
            if (callback)
                callback(null, {
                    code: "DISCONNECTED",
                    message: "Backend disconnected"
                });
            return "";
        }
        if (Object.keys(pending).length >= 28) {
            if (callback)
                callback(null, {
                    code: "BUSY",
                    message: "Too many pending requests; try again"
                });
            return "";
        }
        const id = "qml-" + (++serial);
        pending[id] = {
            callback: callback,
            method: method,
            deadline: Date.now() + 45000
        };
        pendingCount = Object.keys(pending).length;
        requestSent(method, params || {});
        const wire = JSON.stringify({
            v: 1,
            id: id,
            method: method,
            params: params || {}
        }) + "\n";
        if (testTransport)
            testTransport(wire);
        else {
            socket.write(wire);
            socket.flush();
        }
        return id;
    }
    function abandon() {
        const old = pending;
        pending = {};
        pendingCount = 0;
        for (const id of Object.keys(old))
            if (old[id].callback)
                old[id].callback(null, {
                    code: "DISCONNECTED",
                    message: "Connection lost; request outcome may be unknown. Refresh before repeating an action."
                });
    }
    function lost() {
        if (state === "incompatible protocol" || testTransport)
            return;
        state = "disconnected";
        abandon();
        if (!retry.running) {
            retry.interval = backoff;
            retry.start();
            backoff = Math.min(5000, backoff * 2);
        }
    }
    function connectNow() {
        retry.stop();
        if (testTransport)
            return;
        state = "connecting";
        socket.connected = true;
    }
    function receive(line) {
        let msg;
        try {
            if (line.length > 1048576)
                throw Error("Oversized response");
            msg = JSON.parse(line);
        } catch (e) {
            errorText = "Invalid backend message: " + e;
            failed(errorText);
            return;
        }
        if (msg.v !== 1) {
            state = "incompatible protocol";
            errorText = "This frontend requires protocol v1.";
            socket.connected = false;
            abandon();
            return;
        }
        if (msg.event) {
            eventReceived(msg.event, msg.data || {});
            return;
        }
        const p = pending[msg.id];
        if (!p)
            return;
        delete pending[msg.id];
        pendingCount = Object.keys(pending).length;
        if (msg.ok !== true && msg.error?.code === "UNSUPPORTED_VERSION")
            state = "incompatible protocol";
        if (p.callback)
            p.callback(msg.ok ? msg.result : null, msg.ok ? null : msg.error || {
                message: "Malformed response"
            });
    }
    Socket {
        id: socket
        path: root.socketPath
        parser: SplitParser {
            onRead: data => root.receive(data)
        }
        onConnectionStateChanged: {
            if (!connected) {
                root.lost();
                return;
            }
            root.request("system.health", {}, (health, error) => {
                if (error) {
                    root.errorText = error.message;
                    root.state = "backend error";
                    return;
                }
                if (health.protocol !== 1) {
                    root.state = "incompatible protocol";
                    root.errorText = "Backend protocol is incompatible";
                    socket.connected = false;
                    return;
                }
                const restarted = root.instanceId !== "" && root.instanceId !== health.instance_id;
                root.instanceId = health.instance_id;
                root.request("events.subscribe", {
                    topics: ["system", "project", "cortex", "plan", "run"]
                }, (_, err) => {
                    if (err) {
                        root.state = "backend error";
                        root.errorText = err.message;
                        return;
                    }
                    root.backoff = 250;
                    root.errorText = "";
                    root.state = "connected";
                    root.established(restarted);
                });
            });
        }
        onError: {
            root.errorText = "Local socket unavailable. Start cedar-brain serve, or check socket permissions.";
            root.lost();
        }
    }
    Timer {
        id: retry
        onTriggered: root.connectNow()
    }
    Timer {
        interval: 1000
        repeat: true
        running: root.pendingCount > 0
        onTriggered: {
            for (const id of Object.keys(root.pending)) {
                const p = root.pending[id];
                if (p.deadline > Date.now())
                    continue;
                delete root.pending[id];
                root.pendingCount = Object.keys(root.pending).length;
                if (p.callback)
                    p.callback(null, {
                        code: "TIMEOUT",
                        message: p.method + " timed out; its outcome is unknown. Refresh before repeating."
                    });
            }
        }
    }
    Component.onCompleted: connectNow()
}

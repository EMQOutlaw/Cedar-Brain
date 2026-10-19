import QtQuick
import "../core"
import "../core/Models.js" as Models

Canvas {
    id: root
    property var nodes: []
    property var edges: []
    property var geometry: ({
            positions: {},
            rows: []
        })
    property string selectedId: ""
    property real zoom: 1
    property real panX: 80
    property real panY: 70
    property bool rendering: visible
    property int paintedNodes: 0
    signal selected(string id)
    clip: true
    activeFocusOnTab: true
    Accessible.role: Accessible.Canvas
    Accessible.name: "Project graph. Drag to pan, wheel or plus and minus to zoom. Use the node list for keyboard selection."
    function redraw() {
        if (rendering)
            requestPaint();
    }
    function resetView() {
        zoom = 1;
        panX = 80;
        panY = 70;
        redraw();
    }
    function centerSelection() {
        const p = geometry.positions[selectedId];
        if (p) {
            panX = width / 2 - p.x * zoom;
            panY = height / 2 - p.y * zoom;
            redraw();
        }
    }
    function zoomAt(factor, x, y) {
        const next = Math.max(.15, Math.min(3, zoom * factor));
        const ratio = next / zoom;
        panX = x - (x - panX) * ratio;
        panY = y - (y - panY) * ratio;
        zoom = next;
        redraw();
    }
    onNodesChanged: {
        geometry = Models.graphLayout(nodes);
        redraw();
    }
    onEdgesChanged: redraw()
    onSelectedIdChanged: redraw()
    onWidthChanged: redraw()
    onHeightChanged: redraw()
    onRenderingChanged: if (rendering)
        redraw()
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Plus || event.key === Qt.Key_Equal)
            zoomAt(1.2, width / 2, height / 2);
        else if (event.key === Qt.Key_Minus)
            zoomAt(1 / 1.2, width / 2, height / 2);
        else if (event.key === Qt.Key_Left)
            panX += 40;
        else if (event.key === Qt.Key_Right)
            panX -= 40;
        else if (event.key === Qt.Key_Up)
            panY += 40;
        else if (event.key === Qt.Key_Down)
            panY -= 40;
        else {
            event.accepted = false;
            return;
        }
        redraw();
        event.accepted = true;
    }
    onPaint: {
        const c = getContext("2d");
        c.reset();
        c.fillStyle = Theme.background;
        c.fillRect(0, 0, width, height);
        const positions = geometry.positions;
        const x = p => p.x * zoom + panX, y = p => p.y * zoom + panY;
        c.lineWidth = 1;
        c.strokeStyle = Qt.alpha(Theme.accent, .24);
        for (const e of edges) {
            const a = positions[e.source], b = positions[e.target];
            if (!a || !b)
                continue;
            const ax = x(a), ay = y(a), bx = x(b), by = y(b);
            if (Math.max(ax, bx) < 0 || Math.min(ax, bx) > width || Math.max(ay, by) < 0 || Math.min(ay, by) > height)
                continue;
            c.strokeStyle = e.evidence === "static_extracted" ? Qt.alpha(Theme.accent, .4) : Qt.alpha(Theme.amber, .35);
            c.beginPath();
            c.moveTo(ax, ay);
            c.lineTo(bx, by);
            c.stroke();
            if (zoom > .8 && edges.length < 120) {
                const angle = Math.atan2(by - ay, bx - ax);
                const tx = bx - Math.cos(angle) * 10, ty = by - Math.sin(angle) * 10;
                c.beginPath();
                c.moveTo(tx - 6 * Math.cos(angle - .5), ty - 6 * Math.sin(angle - .5));
                c.lineTo(tx, ty);
                c.lineTo(tx - 6 * Math.cos(angle + .5), ty - 6 * Math.sin(angle + .5));
                c.stroke();
            }
        }
        let count = 0;
        for (const p of geometry.rows) {
            const px = x(p), py = y(p);
            if (px < -160 || px > width + 30 || py < -30 || py > height + 30)
                continue;
            count++;
            const selected = p.node.id === selectedId;
            c.fillStyle = selected ? Theme.green : Theme.accent;
            c.beginPath();
            c.moveTo(px, py - 6);
            c.lineTo(px + 6, py);
            c.lineTo(px, py + 6);
            c.lineTo(px - 6, py);
            c.closePath();
            c.fill();
            if (selected) {
                c.strokeStyle = Theme.green;
                c.strokeRect(px - 12, py - 12, 24, 24);
            }
            if (zoom > .45 && (nodes.length < 300 || selected || zoom > 1)) {
                c.font = "12px sans-serif";
                c.fillStyle = selected ? Theme.text : Theme.muted;
                const label = String(p.node.label || p.node.id);
                c.fillText(label.length > 24 ? label.slice(0, 23) + "…" : label, px + 14, py + 4);
            }
        }
        paintedNodes = count;
        if (activeFocus) {
            c.strokeStyle = Theme.accent;
            c.strokeRect(1, 1, width - 2, height - 2);
        }
    }
    onActiveFocusChanged: redraw()
    MouseArea {
        anchors.fill: parent
        property real lastX: 0
        property real lastY: 0
        property real moved: 0
        onPressed: mouse => {
            root.forceActiveFocus();
            lastX = mouse.x;
            lastY = mouse.y;
            moved = 0;
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return;
            const dx = mouse.x - lastX, dy = mouse.y - lastY;
            moved += Math.abs(dx) + Math.abs(dy);
            root.panX += dx;
            root.panY += dy;
            lastX = mouse.x;
            lastY = mouse.y;
            root.redraw();
        }
        onReleased: mouse => {
            if (moved > 5)
                return;
            const wx = (mouse.x - root.panX) / root.zoom, wy = (mouse.y - root.panY) / root.zoom;
            let hit = null, best = 24 / root.zoom;
            for (const p of root.geometry.rows) {
                const d = Math.hypot(p.x - wx, p.y - wy);
                if (d < best) {
                    best = d;
                    hit = p;
                }
            }
            if (hit) {
                root.selectedId = hit.node.id;
                root.selected(hit.node.id);
            }
        }
        onWheel: wheel => root.zoomAt(wheel.angleDelta.y > 0 ? 1.15 : 1 / 1.15, wheel.x, wheel.y)
    }
    Connections {
        target: Theme
        function onPaletteChanged() {
            root.redraw();
        }
        function onSyncThemeChanged() {
            root.redraw();
        }
    }
}

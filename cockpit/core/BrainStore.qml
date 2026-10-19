pragma Singleton
import QtQuick
import Quickshell
import "Models.js" as Models

Singleton {
    id: root
    property var health: ({})
    property var capabilities: ({})
    property var projects: []
    property string projectId: ""
    readonly property var project: projects.find(p => p.id === projectId) || null
    property var projectStatus: ({})
    property var agents: []
    property var agentDetail: null
    property var skills: []
    property var skillInfo: ({})
    property var skillDetail: null
    property var plan: null
    property var definition: null
    property var validation: null
    property bool edited: false
    property bool planning: false
    property bool starting: false
    property string objective: ""
    property var recentRequests: []
    property var runs: []
    property var run: null
    property var workflow: null
    property string selectedStep: ""
    property bool cancelling: false
    property var logs: []
    property var logQueue: []
    property int lastLogSeq: 0
    property bool replaying: false
    property bool runDirty: false
    property var graphStatus: ({})
    property var communities: []
    property var graphNodes: []
    property var graphEdges: []
    property var nodeDetail: null
    property string graphNote: "Search or choose a community to explore static code relationships."
    property bool graphTruncated: false
    property int graphGeneration: 0
    property string errorText: ""
    property int epoch: 0
    property bool initialProject: true
    function supports(feature) {
        return BrainConnection.ready && capabilities.features?.[feature]?.status === "supported";
    }
    function report(error) {
        errorText = (error.code || "ERROR") + ": " + error.message + (error.details ? "\n" + Models.pretty(error.details) : "");
    }
    function call(method, params, callback) {
        return BrainConnection.request(method, params, (result, error) => {
            if (error)
                report(error);
            if (callback)
                callback(result, error);
        });
    }
    function scoped(method, params, callback) {
        const e = epoch;
        call(method, params, (r, err) => {
            if (e === epoch && r)
                callback(r);
        });
    }
    function refresh() {
        call("system.health", {}, r => {
            if (r)
                health = r;
        });
        call("system.capabilities", {}, r => {
            if (r)
                capabilities = r;
        });
        refreshProjects();
        refreshAgents();
        refreshSkills();
        if (plan)
            call("plans.get", {
                plan_id: plan.id
            }, r => {
                if (r)
                    setPlan(r);
            });
        if (run) {
            fetchRun();
            replayLogs();
        }
    }
    function refreshProjects() {
        call("projects.list", {}, r => {
            if (!r)
                return;
            projects = r.projects;
            if (initialProject) {
                initialProject = false;
                if (Theme.defaultProject && Theme.defaultProject !== r.active_project_id && projects.some(p => p.id === Theme.defaultProject)) {
                    selectProject(Theme.defaultProject);
                    return;
                }
            }
            const changed = projectId !== (r.active_project_id || "");
            projectId = r.active_project_id || "";
            if (!changed)
                refreshProject();
        });
    }
    onProjectIdChanged: {
        epoch++;
        graphGeneration++;
        projectStatus = {};
        graphStatus = {};
        graphNodes = [];
        graphEdges = [];
        communities = [];
        nodeDetail = null;
        plan = null;
        definition = null;
        validation = null;
        planning = false;
        run = null;
        workflow = null;
        logs = [];
        logQueue = [];
        lastLogSeq = 0;
        runs = [];
        selectedStep = "";
        NavigationState.inspectorOpen = false;
        refreshProject();
    }
    function refreshProject() {
        if (!projectId || !BrainConnection.ready)
            return;
        scoped("projects.status", {
            project_id: projectId
        }, r => projectStatus = r);
        scoped("cortex.status", {
            project_id: projectId
        }, r => {
            graphStatus = r;
            if (r.loaded)
                scoped("cortex.communities", {
                    limit: 100
                }, c => communities = c.communities || []);
        });
        refreshRuns();
    }
    function selectProject(id) {
        call("projects.select", {
            project_id: id
        }, r => {
            if (r)
                refreshProjects();
        });
    }
    function addProject(path) {
        call("projects.add", {
            path: path,
            select: true
        }, r => {
            if (r)
                refreshProjects();
        });
    }
    function refreshAgents(force) {
        call("agents.list", {
            refresh: !!force
        }, r => {
            if (r)
                agents = r.agents;
        });
    }
    function inspectAgent(id) {
        call("agents.status", {
            agent_id: id
        }, r => {
            if (!r)
                return;
            agentDetail = r;
            NavigationState.inspect(r.name, Models.agentText(r), "agent", id);
        });
    }
    function refreshSkills(force) {
        call("skills.list", {
            refresh: !!force
        }, r => {
            if (r) {
                skills = r.skills;
                skillInfo = r;
            }
        });
    }
    function inspectSkill(id) {
        call("skills.inspect", {
            skill_id: id
        }, r => {
            if (!r)
                return;
            skillDetail = r;
            NavigationState.inspect(r.name, "Trust: " + r.trust + " · " + r.compatibility + "\nMetadata does not establish safety.\n\nSource: " + r.source + "\nVersion: " + (r.version || "not declared") + "\nRequirements: " + Models.pretty(r.requirements) + "\n\n" + (r.notice || "") + "\nProblems: " + Models.pretty(r.problems) + "\n\n" + (r.instructions || "No instructions") + (r.instructions_truncated ? "\n[Instructions truncated by backend]" : ""), "skill", id);
        });
    }
    function importSkill(path) {
        call("skills.import_local", {
            path: path
        }, r => {
            if (r)
                refreshSkills(true);
        });
    }
    function createPlan() {
        if (planning || !supports("pathfinder.plans") || !projectId)
            return;
        planning = true;
        edited = false;
        errorText = "";
        const e = epoch;
        recentRequests = [objective].concat(recentRequests.filter(x => x !== objective)).slice(0, 8);
        call("plans.create", {
            objective: objective,
            project_id: projectId,
            agent_id: Theme.preferredAgent
        }, (r, err) => {
            if (e !== epoch)
                return;
            planning = false;
            if (r) {
                setPlan(r);
                if (r.status === "generating")
                    call("plans.get", {
                        plan_id: r.id
                    }, current => {
                        if (current && e === epoch)
                            setPlan(current);
                    });
            }
        });
    }
    function setPlan(r) {
        plan = r;
        planning = r.status === "generating";
        if (!edited) {
            definition = r.plan;
            validation = r.validation;
        }
    }
    function editDefinition(text) {
        try {
            definition = Models.normalDefinition(JSON.parse(text));
            edited = true;
            validation = null;
            errorText = "";
        } catch (e) {
            errorText = "Invalid plan JSON: " + e;
        }
    }
    function validatePlan() {
        if (!definition)
            return;
        call("plans.validate", {
            plan: Models.normalDefinition(definition),
            project_id: projectId
        }, r => {
            if (r) {
                validation = r;
                if (r.valid)
                    definition = r.plan;
            }
        });
    }
    function approvePlan() {
        if (starting || !validation?.valid || !validation?.executable || !plan || plan.project_id !== projectId)
            return;
        starting = true;
        const e = epoch;
        const p = edited ? {
            definition: Models.normalDefinition(definition),
            project_id: projectId
        } : {
            plan_id: plan.id,
            project_id: projectId
        };
        call("workflows.create", p, (w, err) => {
            if (!w || e !== epoch) {
                starting = false;
                return;
            }
            // Invoked only by the explicit Approve and start control.
            call("runs.start", {
                workflow_id: w.id,
                approve: true
            }, (r, error) => {
                starting = false;
                if (!r || e !== epoch)
                    return;
                workflow = w;
                selectRun(r.id);
                NavigationState.page = 3;
                refreshRuns();
            });
        });
    }
    function refreshRuns() {
        if (projectId)
            scoped("runs.list", {
                project_id: projectId,
                limit: 100
            }, r => runs = r.runs);
    }
    function selectRun(id) {
        run = {
            id: id,
            steps: []
        };
        workflow = null;
        selectedStep = "";
        logs = [];
        logQueue = [];
        lastLogSeq = 0;
        replaying = false;
        fetchRun();
        replayLogs();
    }
    function fetchRun() {
        if (!run)
            return;
        const id = run.id;
        call("runs.get", {
            run_id: id
        }, r => {
            if (!r || run?.id !== id)
                return;
            run = r;
            if (!Models.active(r.status))
                cancelling = false;
            if (workflow?.id !== r.workflow_id)
                call("workflows.get", {
                    workflow_id: r.workflow_id
                }, w => {
                    if (w && run?.id === id)
                        workflow = w;
                });
            updateStepInspector();
        });
    }
    function replayLogs() {
        if (!run || replaying || !BrainConnection.ready)
            return;
        replaying = true;
        const id = run.id;
        function page(after) {
            call("runs.events", {
                run_id: id,
                after_seq: after,
                limit: 1000
            }, (r, err) => {
                if (run?.id !== id) {
                    replaying = false;
                    return;
                }
                if (!r) {
                    replaying = false;
                    return;
                }
                for (const ev of r.events)
                    if (ev.event === "run.output")
                        logQueue.push(Object.assign({
                            seq: ev.seq
                        }, ev.data));
                const end = r.events.length ? r.events[r.events.length - 1].seq : after;
                lastLogSeq = Math.max(lastLogSeq, end);
                flush.start();
                if (r.more && end > after)
                    page(end);
                else
                    replaying = false;
            });
        }
        page(lastLogSeq);
    }
    function cancelRun() {
        if (!run || cancelling)
            return;
        cancelling = true;
        const id = run.id;
        call("runs.cancel", {
            run_id: id
        }, (r, err) => {
            if (run?.id !== id)
                return;
            if (r) {
                run = r;
                cancelling = false;
            } else {
                cancelling = false;
                fetchRun();
            }
        });
    }
    function approveStep(id, approve) {
        if (run)
            call("runs.approve", {
                run_id: run.id,
                step_id: id,
                approve: approve
            }, () => fetchRun());
    }
    function inspectStep(id) {
        selectedStep = id;
        updateStepInspector(true);
    }
    function updateStepInspector(open) {
        if (!selectedStep || (!open && (!NavigationState.inspectorOpen || NavigationState.inspectorKind !== "step")))
            return;
        const s = run?.steps?.find(x => x.step_id === selectedStep);
        if (!s)
            return;
        const d = workflow?.definition?.steps?.find(x => x.id === selectedStep);
        const text = Models.stepText(s, d, BrainConnection.ready) + "\n\nRECENT OUTPUT\nLast 120 step messages; display capped at 64 KiB\n" + logs.filter(x => x.step_id === selectedStep).slice(-120).map(x => "[" + x.kind + "] " + x.text).join("\n").slice(-65536);
        if (open)
            NavigationState.inspect(selectedStep, text, "step", selectedStep);
        else
            NavigationState.inspectorText = text;
    }
    function queryGraph(query) {
        const generation = ++graphGeneration;
        scoped("cortex.search", {
            query: query,
            limit: 200
        }, r => {
            if (generation !== graphGeneration)
                return;
            graphNodes = r.results;
            graphEdges = [];
            graphTruncated = r.results.length === 200;
            graphNote = "Search results · select a node to load verified relationships";
        });
    }
    function communityGraph(id) {
        const generation = ++graphGeneration;
        scoped("cortex.communities", {
            community_id: id,
            limit: 200
        }, r => {
            if (generation !== graphGeneration)
                return;
            graphNodes = r.members || [];
            graphEdges = [];
            graphTruncated = !!r.truncated;
            graphNote = "Community members · select a node to explore relationships";
        });
    }
    function inspectNode(id) {
        scoped("cortex.node", {
            node_id: id
        }, r => {
            nodeDetail = r;
            NavigationState.inspect(r.node.label, Models.nodeText(r), "node", id);
        });
    }
    function dependencies(direction, relation) {
        if (!nodeDetail)
            return;
        const n = nodeDetail.node;
        const generation = ++graphGeneration;
        const p = {
            node_id: n.id,
            direction: direction || "both",
            depth: 2,
            limit: 200
        };
        if (relation)
            p.relations = [relation];
        scoped("cortex.dependencies", p, r => {
            if (generation !== graphGeneration)
                return;
            graphNodes = [n].concat(r.nodes.filter(x => x.id !== n.id));
            graphEdges = r.edges;
            graphTruncated = r.truncated;
            graphNote = r.note;
        });
    }
    function impact() {
        if (nodeDetail)
            scoped("cortex.impact", {
                node_ids: [nodeDetail.node.id],
                depth: 2,
                limit: 200
            }, r => NavigationState.inspect("Impact candidates", "Candidates for review, not confirmed breakage.\n\n" + Models.pretty(r), "impact", ""));
    }
    Connections {
        target: BrainConnection
        function onEstablished(restarted) {
            root.refresh();
        }
        function onFailed(message) {
            root.report({
                code: "INVALID_MESSAGE",
                message: message
            });
        }
        function onEventReceived(name, data) {
            if (name === "stream.gap") {
                root.refresh();
                return;
            }
            if (name === "project.updated") {
                projectRefresh.start();
                return;
            }
            if (name === "cortex.reloaded") {
                root.refreshProject();
                return;
            }
            if (name === "plan.updated" && root.plan?.id === data.plan_id)
                root.call("plans.get", {
                    plan_id: data.plan_id
                }, r => {
                    if (r)
                        root.setPlan(r);
                });
            if (name.startsWith("run.")) {
                if (name === "run.updated")
                    historyRefresh.start();
                if (root.run?.id !== data.run_id)
                    return;
                if (data.seq > root.lastLogSeq + 1)
                    root.replayLogs();
                if (name === "run.output")
                    root.logQueue = Models.boundedAppend(root.logQueue, [data], 1000);
                else
                    root.runDirty = true;
                if (!root.replaying)
                    root.lastLogSeq = Math.max(root.lastLogSeq, data.seq || 0);
                flush.start();
            }
        }
    }
    Timer {
        id: projectRefresh
        interval: 150
        onTriggered: root.refreshProjects()
    }
    Timer {
        id: historyRefresh
        interval: 250
        onTriggered: {
            root.refreshRuns();
            root.refreshAgents();
        }
    }
    Timer {
        id: flush
        interval: 120
        onTriggered: {
            const unique = {};
            for (const x of root.logs.concat(root.logQueue))
                unique[x.seq] = x;
            root.logs = Object.values(unique).sort((a, b) => a.seq - b.seq).slice(-400);
            root.logQueue = [];
            root.updateStepInspector();
            if (root.runDirty) {
                root.runDirty = false;
                root.fetchRun();
            }
        }
    }
}

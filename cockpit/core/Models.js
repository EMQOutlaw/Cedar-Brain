.pragma library
function pretty(value) { return value === undefined || value === null ? "Not available" : typeof value === "string" ? value : JSON.stringify(value, null, 2); }
function boundedAppend(buffer, events, limit) { return buffer.concat(events).slice(-limit); }
function active(status) { return ["queued", "running", "awaiting_approval"].includes(status); }
function normalDefinition(definition) {
    const d = JSON.parse(JSON.stringify(definition));
    for (const step of d.steps || []) if (step.timeout_s === null) delete step.timeout_s;
    return d;
}
function graphLayout(nodes) {
    // Stable geometry computed only when a query changes, never during pan/zoom.
    const groups = {}; const positions = {}; const rows = [];
    for (const n of nodes) { const k = String(n.community ?? "unknown"); if (!groups[k]) groups[k] = []; groups[k].push(n); }
    let baseX = 0;
    for (const key of Object.keys(groups)) {
        const group = groups[key]; const columns = Math.max(1, Math.ceil(Math.sqrt(group.length)));
        group.forEach((n, i) => { const p = {node:n, x:baseX + (i % columns) * 190, y:Math.floor(i / columns) * 100}; positions[n.id] = p; rows.push(p); });
        baseX += columns * 190 + 100;
    }
    return {positions:positions, rows:rows};
}

function relationships(edges) {
    return (edges || []).map(e => e.source + " → " + e.target + "\n  " + e.relation + " · " + e.evidence).join("\n\n") || "None in this result";
}
function nodeText(r) {
    const n = r.node;
    return "Type: " + n.file_type + "\nSource: " + n.source_file + (n.source_location ? " · " + n.source_location : "") + "\nCommunity: " + (n.community_name || n.community) + "\n\nSource file: " + (r.source?.exists ? "present" : "unavailable") + "\n" + (r.source?.path || "No accessible source path") + "\n\nOUTGOING · " + r.outgoing_total + "\n" + relationships(r.outgoing) + "\n\nINCOMING · " + r.incoming_total + "\n" + relationships(r.incoming) + "\n\nStatic code relationships, not proof of runtime dependencies. Inferred and ambiguous evidence needs review.";
}
function agentText(r) {
    return "CLI: " + r.status + "\nExecutable: " + (r.executable || "not found") + "\nVersion: " + (r.version || "not detected") + "\nAuthentication: " + (r.authenticated === null ? "not verified" : r.authenticated ? "verified by CLI check" : "not authenticated") + "\nModes: " + (r.modes || []).join(", ") + "\nActive jobs: " + r.active_jobs + "\nConcurrent process limit: " + r.max_concurrent_processes + "\n\n" + (r.detail || "No detection error reported") + "\n\nRECENT JOBS\n" + ((r.recent_jobs || []).map(j => j.id + " · " + j.status + "\n" + j.owner_kind + " / " + j.owner_id + "\nSession: " + (j.session_id || "not assigned")).join("\n\n") || "No recent jobs");
}
function stepText(s, d, live) {
    const verification = (s.verification || []).map(v => (v.passed ? "PASS" : "FAIL") + " · " + v.method + "\n" + pretty(v.detail)).join("\n") || "Not yet validated";
    const result = s.result ? (s.result.output || pretty(s.result.data || s.result)) : "No result yet";
    const job = s.result?.job;
    return (d?.description || s.step_id) + "\n\nExecutor: " + s.executor + "\nState: " + s.state + (live ? "" : " (last known; offline)") + "\n\nINPUTS\n" + pretty(d?.inputs) + "\n\nVALIDATION\n" + verification + "\n\n" + (s.error ? "ERROR\n" + pretty(s.error) + "\n\n" : "") + (s.worktree ? "ISOLATED WORKTREE\n" + pretty(s.worktree) + "\n\n" : "") + "RESULT\n" + String(result).slice(0,65536) + (String(result).length > 65536 ? "\n[Display capped at 64 KiB]" : "") + (job ? "\n\nJOB\n" + job.job_id + " · " + job.status + "\nExit: " + job.exit_code + "\nStdout log: " + job.stdout_log + "\nStderr log: " + job.stderr_log : "");
}

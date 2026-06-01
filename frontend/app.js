/**
 * Store Intelligence — Dashboard Application
 *
 * Polls the API every 3 seconds and updates all dashboard panels.
 * Uses vanilla JS — no frameworks, no build step.
 */

const API_BASE = window.location.origin;
const STORE_ID = 'purplle-001';
const POLL_INTERVAL = 3000;

// ── State ────────────────────────────────────────────────────────────
let prevMetrics = {};
let tickCount = 0;

// ── Helpers ──────────────────────────────────────────────────────────
async function fetchJSON(path) {
    try {
        const resp = await fetch(`${API_BASE}${path}`);
        if (resp.ok) return await resp.json();
    } catch (e) { /* swallow */ }
    return null;
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function animateValue(el, newVal) {
    const current = el.textContent;
    const formatted = typeof newVal === 'number'
        ? (Number.isInteger(newVal) ? newVal.toLocaleString() : newVal.toFixed(1) + '%')
        : String(newVal ?? '—');

    if (current !== formatted) {
        el.textContent = formatted;
        el.classList.add('updated');
        setTimeout(() => el.classList.remove('updated'), 600);
    }
}

function formatTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── Clock ────────────────────────────────────────────────────────────
function updateClock() {
    const now = new Date();
    $('#clock').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

// ── KPI Update ───────────────────────────────────────────────────────
function updateKPIs(metrics) {
    if (!metrics) return;

    animateValue($('[data-counter="unique_visitors"]'), metrics.unique_visitors);
    animateValue($('[data-counter="current_inside"]'), metrics.current_inside);
    animateValue($('[data-counter="conversion_pct"]'), metrics.conversion_pct != null ? metrics.conversion_pct : '—');
    animateValue($('[data-counter="queue_depth"]'), metrics.queue_depth);
    animateValue($('[data-counter="total_events"]'), metrics.total_events);

    // Subs
    $('#kpi-customers-sub').textContent = `${metrics.unique_customers} customers · ${metrics.staff_count} staff`;
    $('#kpi-inside-sub').textContent = `${metrics.entries} in · ${metrics.exits} out`;
    $('#kpi-conv-sub').textContent = metrics.conversion_pct != null ? `${metrics.unique_customers} of ${metrics.unique_visitors}` : 'No data';
    $('#kpi-queue-sub').textContent = metrics.queue_depth > 3 ? '⚠ High' : 'Normal';
    $('#kpi-events-sub').textContent = metrics.last_event_at ? `Last: ${formatTime(metrics.last_event_at)}` : '';

    prevMetrics = metrics;
}

// ── Funnel Update ────────────────────────────────────────────────────
function updateFunnel(funnel) {
    if (!funnel) return;
    const container = $('#funnel-container');
    const stages = funnel.stages || [];
    const maxCount = Math.max(...stages.map(s => s.count), 1);

    const stageLabels = {
        entered: 'Entered Store',
        browsed_zone: 'Browsed Zone',
        engaged_dwell: 'Engaged (Dwell)',
        reached_checkout: 'Reached Checkout',
        purchased: 'Purchased',
    };

    let html = '';
    stages.forEach((stage, i) => {
        const pct = Math.max((stage.count / maxCount) * 100, stage.count > 0 ? 8 : 2);
        const dropClass = stage.drop_off_pct > 60 ? 'high' : stage.drop_off_pct > 30 ? 'medium' : 'low';
        const dropText = stage.drop_off_pct > 0 ? `-${stage.drop_off_pct}%` : '—';

        html += `
            <div class="funnel-stage">
                <div class="funnel-label">${stageLabels[stage.stage] || stage.stage}</div>
                <div class="funnel-bar-wrap">
                    <div class="funnel-bar stage-${i}" style="width: ${pct}%">
                        <span class="funnel-bar-value">${stage.count}</span>
                    </div>
                </div>
                <div class="funnel-drop ${dropClass}">${dropText}</div>
            </div>`;
    });

    container.innerHTML = html;
    $('#funnel-conv-badge').textContent = `${funnel.overall_conversion_pct}% conv`;
}

// ── Heatmap Update ───────────────────────────────────────────────────
function updateHeatmap(heatmap) {
    if (!heatmap || !heatmap.heatmap.length) {
        $('#heatmap-container').innerHTML = '<div class="funnel-loading">No zone data yet</div>';
        return;
    }

    const zones = heatmap.heatmap;
    const buckets = heatmap.time_buckets;

    // Shorten bucket labels
    const bucketLabels = buckets.map(b => {
        const d = new Date(b);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });

    let html = '<table class="heatmap-table"><thead><tr><th class="zone-header">Zone</th>';
    bucketLabels.forEach(l => { html += `<th>${l}</th>`; });
    html += '</tr></thead><tbody>';

    zones.forEach(zone => {
        html += `<tr><th class="zone-header">${zone.zone_id}</th>`;
        zone.normalized.forEach((val, i) => {
            const raw = zone.raw_counts[i];
            const hue = 260 - val * 200; // Purple(260) → Green(60)
            const sat = 70 + val * 30;
            const light = 8 + val * 32;
            const alpha = 0.2 + val * 0.7;
            const bg = `hsla(${hue}, ${sat}%, ${light}%, ${alpha})`;
            html += `<td class="heatmap-cell" style="background:${bg}" title="${zone.zone_id}: ${raw} visits">${raw || ''}</td>`;
        });
        html += '</tr>';
    });

    html += '</tbody></table>';
    $('#heatmap-container').innerHTML = html;
}

// ── Active Visitors Update ───────────────────────────────────────────
function updateVisitors(active) {
    if (!active) return;
    const list = $('#visitors-list');
    const visitors = active.visitors || [];
    $('#active-badge').textContent = active.active_count;

    if (!visitors.length) {
        list.innerHTML = '<div class="funnel-loading">No active visitors</div>';
        return;
    }

    let html = '';
    visitors.slice(0, 12).forEach(v => {
        const isStaff = v.is_staff;
        const avatarClass = isStaff ? 'staff' : 'customer';
        const badgeClass = isStaff ? 'staff-badge' : 'customer-badge';
        const badgeText = isStaff ? 'Staff' : 'Customer';
        const icon = isStaff ? '👔' : '🛍';
        const shortId = v.visitor_id.replace('visitor-', '#');

        html += `
            <div class="visitor-row">
                <div class="visitor-avatar ${avatarClass}">${icon}</div>
                <div class="visitor-info">
                    <div class="visitor-name">${shortId}</div>
                    <div class="visitor-zone">${v.zone_id || 'unknown'}</div>
                </div>
                <span class="visitor-badge ${badgeClass}">${badgeText}</span>
            </div>`;
    });

    if (visitors.length > 12) {
        html += `<div class="visitor-row" style="justify-content:center;color:var(--text-muted);font-size:0.75rem;">+${visitors.length - 12} more</div>`;
    }

    list.innerHTML = html;
}

// ── Anomalies Update ─────────────────────────────────────────────────
function updateAnomalies(anomalies) {
    if (!anomalies) return;
    const list = $('#anomalies-list');
    const items = anomalies.anomalies || [];
    const badge = $('#anomaly-badge');
    badge.textContent = items.length;
    badge.className = 'card-badge ' + (items.length > 0 ? 'anomaly-count' : '');

    if (!items.length) {
        list.innerHTML = `
            <div class="no-anomalies">
                <div class="no-anomalies-icon">✅</div>
                <div class="no-anomalies-text">No anomalies detected</div>
            </div>`;
        return;
    }

    const severityIcons = { high: '🔴', medium: '🟡', low: '🔵' };
    const severityColors = { high: 'var(--accent-red)', medium: 'var(--accent-orange)', low: 'var(--accent-blue)' };

    let html = '';
    items.forEach(a => {
        const icon = severityIcons[a.severity] || '⚪';
        html += `
            <div class="anomaly-item severity-${a.severity}">
                <div class="anomaly-header">
                    <span>${icon}</span>
                    <span class="anomaly-severity" style="color:${severityColors[a.severity]}">${a.severity}</span>
                    <span class="anomaly-type">${a.type.replace(/_/g, ' ')}</span>
                </div>
                <div class="anomaly-desc">${a.description}</div>
                <div class="anomaly-rule">Rule: ${a.rule}</div>
            </div>`;
    });

    list.innerHTML = html;
}

// ── Dwell Update ─────────────────────────────────────────────────────
function updateDwell(metrics) {
    if (!metrics) return;
    const container = $('#dwell-container');
    const zones = metrics.avg_dwell_by_zone || [];

    if (!zones.length) {
        container.innerHTML = '<div class="funnel-loading">No dwell data</div>';
        return;
    }

    const maxDwell = Math.max(...zones.map(z => z.avg_dwell_ms), 1);

    let html = '';
    zones.sort((a, b) => b.avg_dwell_ms - a.avg_dwell_ms).forEach(z => {
        const secs = z.avg_dwell_ms / 1000;
        const pct = Math.max((z.avg_dwell_ms / maxDwell) * 100, 5);
        const barClass = secs < 10 ? 'short' : secs < 20 ? 'medium' : 'long';

        html += `
            <div class="dwell-row">
                <div class="dwell-zone">${z.zone_id}</div>
                <div class="dwell-bar-wrap">
                    <div class="dwell-bar ${barClass}" style="width: ${pct}%">
                        <span class="dwell-value">${secs.toFixed(1)}s</span>
                    </div>
                </div>
            </div>`;
    });

    container.innerHTML = html;
}

// ── Events Breakdown ─────────────────────────────────────────────────
function updateEvents(metrics) {
    if (!metrics) return;
    const grid = $('#events-grid');
    const breakdown = metrics.event_type_breakdown || {};

    const icons = {
        ENTRY: '🚪', EXIT: '🚶', ZONE_ENTER: '📍', ZONE_EXIT: '📤',
        ZONE_DWELL: '⏱', BILLING_QUEUE_JOIN: '🧾', BILLING_QUEUE_ABANDON: '❌', REENTRY: '🔄',
    };

    const order = ['ENTRY', 'EXIT', 'ZONE_ENTER', 'ZONE_EXIT', 'ZONE_DWELL', 'BILLING_QUEUE_JOIN', 'BILLING_QUEUE_ABANDON', 'REENTRY'];

    let html = '';
    order.forEach(type => {
        const count = breakdown[type] || 0;
        if (count === 0 && !['ENTRY', 'EXIT'].includes(type)) return;
        html += `
            <div class="event-chip">
                <span class="event-icon">${icons[type] || '•'}</span>
                <div class="event-info">
                    <div class="event-type-name">${type.replace(/_/g, ' ')}</div>
                    <div class="event-count">${count}</div>
                </div>
            </div>`;
    });

    grid.innerHTML = html;
}

// ── Health / Status ──────────────────────────────────────────────────
function updateStatus(health) {
    const dot = $('#status-dot');
    const text = $('#status-text');

    if (health && health.status === 'ok') {
        dot.className = 'status-indicator';
        text.textContent = `Connected · ${health.total_events} events`;
    } else {
        dot.className = 'status-indicator disconnected';
        text.textContent = health ? 'Degraded' : 'Disconnected';
    }
}

// ── Main Poll Loop ───────────────────────────────────────────────────
async function poll() {
    tickCount++;

    const [health, metrics, funnel, active, anomalies] = await Promise.all([
        fetchJSON('/health'),
        fetchJSON(`/stores/${STORE_ID}/metrics`),
        fetchJSON(`/stores/${STORE_ID}/funnel`),
        fetchJSON(`/visitors/active?store_id=${STORE_ID}`),
        fetchJSON(`/stores/${STORE_ID}/anomalies?dwell_multiplier=1.5`),
    ]);

    updateStatus(health);
    updateKPIs(metrics);
    updateFunnel(funnel);
    updateVisitors(active);
    updateAnomalies(anomalies);
    updateDwell(metrics);
    updateEvents(metrics);

    // Heatmap — poll less frequently (every 5 ticks)
    if (tickCount % 5 === 1) {
        const heatmap = await fetchJSON(`/stores/${STORE_ID}/heatmap?bucket_minutes=1`);
        updateHeatmap(heatmap);
    }

    $('#last-update').textContent = `Last poll: ${new Date().toLocaleTimeString()}`;
}

// ── Init ─────────────────────────────────────────────────────────────
poll();
setInterval(poll, POLL_INTERVAL);

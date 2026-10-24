#!/usr/bin/env python3
"""
WiFi Mapper — Companion LAN Speed Test Server & Live Floorplan/Heatmap Dashboard
Runs on Windows/macOS/Linux with zero third-party dependencies (Python 3.10+ stdlib).

Endpoints used by the iOS WiFiMapper app:
  GET  /api/ping                  -> Low-overhead RTT & jitter measurement
  GET  /api/download?bytes=N      -> High-speed local Wi-Fi download throughput stream
  POST /api/upload                -> High-speed local Wi-Fi upload throughput receiver
  POST /api/sync                  -> Receives SurveyProject JSON from the iPhone app
  GET  /api/project               -> Returns current SurveyProject JSON + RF optimization
  POST /api/optimize              -> Computes Multi-Wall RF placement recommendations
  GET  /                          -> Interactive 2D House Heatmap & Optimizer Web UI
"""

from __future__ import annotations
import argparse
import json
import math
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Pre-allocated 1 MB buffer for zero-copy LAN speed burst generation
SPEED_CHUNK = b"W" * (1024 * 1024)


def get_local_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def default_demo_project() -> dict:
    """Returns the default 2-Bedroom Demo House survey matching the iOS app."""
    walls = [
        {"id": "w1", "start": {"x": -6, "y": -4}, "end": {"x": 6, "y": -4}, "material": "Brick / Masonry", "isDoorway": False},
        {"id": "w2", "start": {"x": 6, "y": -4}, "end": {"x": 6, "y": 4}, "material": "Brick / Masonry", "isDoorway": False},
        {"id": "w3", "start": {"x": 6, "y": 4}, "end": {"x": -6, "y": 4}, "material": "Brick / Masonry", "isDoorway": False},
        {"id": "w4", "start": {"x": -6, "y": 4}, "end": {"x": -6, "y": -4}, "material": "Brick / Masonry", "isDoorway": False},
        {"id": "w5", "start": {"x": -1.0, "y": -4.0}, "end": {"x": -1.0, "y": -0.6}, "material": "Drywall / Plaster", "isDoorway": False},
        {"id": "w6", "start": {"x": -1.0, "y": -0.6}, "end": {"x": -1.0, "y": 0.6}, "material": "Wood / Door", "isDoorway": True},
        {"id": "w7", "start": {"x": -1.0, "y": 0.6}, "end": {"x": -1.0, "y": 4.0}, "material": "Drywall / Plaster", "isDoorway": False},
        {"id": "w8", "start": {"x": 2.5, "y": -4.0}, "end": {"x": 2.5, "y": -0.8}, "material": "Brick / Masonry", "isDoorway": False},
        {"id": "w9", "start": {"x": 2.5, "y": 0.4}, "end": {"x": 2.5, "y": 4.0}, "material": "Drywall / Plaster", "isDoorway": False},
        {"id": "w10", "start": {"x": -1.0, "y": 0.2}, "end": {"x": 6.0, "y": 0.2}, "material": "Drywall / Plaster", "isDoorway": False},
    ]
    nodes = [
        {
            "id": "router-1",
            "name": "Living Room ISP Router",
            "role": "Primary Router",
            "position": {"x": -5.2, "y": 3.1},
            "heightMeters": 1.0,
            "band": "5 GHz",
            "txPowerDbm": 21.0,
            "backhaul": "Wired Ethernet Backhaul",
        }
    ]
    room_labels = [
        {"id": "r1", "name": "Living Room", "position": {"x": -3.6, "y": 1.5}},
        {"id": "r2", "name": "Kitchen", "position": {"x": -3.6, "y": -2.2}},
        {"id": "r3", "name": "Hallway", "position": {"x": 0.7, "y": -1.8}},
        {"id": "r4", "name": "Master Bedroom", "position": {"x": 4.2, "y": 2.2}},
        {"id": "r5", "name": "Home Office", "position": {"x": 4.2, "y": -2.2}},
    ]
    waypoints = [
        {"x": -5.0, "y": 2.8}, {"x": -4.2, "y": 2.0}, {"x": -3.5, "y": 0.8},
        {"x": -3.8, "y": -1.5}, {"x": -2.5, "y": -2.5}, {"x": -1.2, "y": 0.0},
        {"x": 0.5, "y": 0.0}, {"x": 0.8, "y": 2.2}, {"x": 1.2, "y": -2.0},
        {"x": 3.8, "y": 2.2}, {"x": 4.8, "y": 2.8}, {"x": 4.2, "y": -2.0},
        {"x": 5.1, "y": -2.8},
    ]
    samples = []
    for i, pt in enumerate(waypoints):
        rssi, mbps = evaluate_network_at_point(pt, nodes, walls)
        samples.append({
            "id": f"sample-{i}",
            "position": pt,
            "rssiDbm": int(round(rssi)),
            "isEstimatedRssi": False,
            "downloadMbps": round(mbps, 1),
            "uploadMbps": round(mbps * 0.45, 1),
            "latencyMs": 5.2 if rssi >= -65 else (19.5 if rssi >= -74 else 68.0),
            "jitterMs": 1.2 if rssi >= -65 else 8.4,
            "packetLossPercent": 0.0 if rssi >= -75 else 3.5,
        })

    project = {
        "name": "2-Bedroom Home Survey",
        "walls": walls,
        "nodes": nodes,
        "roomLabels": room_labels,
        "walkedTrajectory": waypoints,
        "samples": samples,
    }
    project["recommendations"] = compute_recommendations(project)
    return project


def segments_intersect(p1: dict, p2: dict, p3: dict, p4: dict) -> bool:
    def ccw(A: dict, B: dict, C: dict) -> float:
        return (C["y"] - A["y"]) * (B["x"] - A["x"]) - (B["y"] - A["y"]) * (C["x"] - A["x"])

    d1 = ccw(p3, p4, p1)
    d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3)
    d4 = ccw(p1, p2, p4)
    return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    )


def predict_rssi(node: dict, target: dict, walls: list[dict]) -> float:
    dx = node["position"]["x"] - target["x"]
    dy = node["position"]["y"] - target["y"]
    dist = max(0.6, math.hypot(dx, dy))
    band = node.get("band", "5 GHz")
    l0 = 40.0 if "2.4" in band else (48.0 if "6" in band else 46.5)
    n = 2.7 if "2.4" in band else (3.35 if "6" in band else 3.1)
    dist_loss = 10.0 * n * math.log10(dist)

    wall_loss = 0.0
    walls_crossed = 0
    for w in walls:
        if segments_intersect(node["position"], target, w["start"], w["end"]):
            walls_crossed += 1
            if w.get("isDoorway"):
                wall_loss += 1.5
            else:
                mat = w.get("material", "Drywall")
                if "Concrete" in mat:
                    wall_loss += 12.0
                elif "Brick" in mat:
                    wall_loss += 8.5
                else:
                    wall_loss += 4.0

    effective_wall_loss = wall_loss * (walls_crossed ** -0.05) if walls_crossed > 0 else 0.0
    indoor_clutter_margin = 3.5 if "2.4" in band else 5.5
    tx = float(node.get("txPowerDbm", 21.0))
    return max(-96.0, min(-28.0, tx - l0 - dist_loss - indoor_clutter_margin - effective_wall_loss))


def estimate_mbps_from_rssi(rssi: float, max_mbps: float = 680.0) -> float:
    if rssi <= -88.0:
        return 0.5
    if rssi >= -42.0:
        return max_mbps
    factor = 1.0 / (1.0 + math.exp(-0.165 * (rssi - (-62.5))))
    return max(1.0, min(max_mbps, max_mbps * factor))


def evaluate_network_at_point(point: dict, nodes: list[dict], walls: list[dict]) -> tuple[float, float]:
    if not nodes:
        return -90.0, 0.0
    primary = next((n for n in nodes if "Primary" in n.get("role", "")), nodes[0])
    best_rssi = -96.0
    best_mbps = 0.0

    for node in nodes:
        client_rssi = predict_rssi(node, point, walls)
        backhaul_eff = 1.0
        if node.get("id") != primary.get("id") and "Wireless" in node.get("backhaul", "Wireless"):
            bh_rssi = predict_rssi(primary, node["position"], walls)
            if bh_rssi >= -58.0:
                backhaul_eff = 0.85
            elif bh_rssi >= -67.0:
                backhaul_eff = 0.68
            elif bh_rssi >= -74.0:
                backhaul_eff = 0.38
            else:
                backhaul_eff = 0.12
        mbps = estimate_mbps_from_rssi(client_rssi) * backhaul_eff
        if client_rssi > best_rssi:
            best_rssi = client_rssi
            best_mbps = mbps

    return best_rssi, best_mbps


def compute_recommendations(project: dict) -> list[dict]:
    walls = project.get("walls", [])
    nodes = project.get("nodes", [])
    eval_points = [
        {"x": round(x * 0.8, 2), "y": round(y * 0.8, 2)}
        for x in range(-7, 8)
        for y in range(-4, 5)
    ]

    def coverage_stats(test_nodes: list[dict]) -> tuple[float, float]:
        good, dead = 0, 0
        for pt in eval_points:
            r, _ = evaluate_network_at_point(pt, test_nodes, walls)
            if r >= -67.0:
                good += 1
            elif r < -75.0:
                dead += 1
        total = max(1, len(eval_points))
        return round(good * 100.0 / total, 1), round(dead * 100.0 / total, 1)

    base_good, base_dead = coverage_stats(nodes)
    primary = next(
        (n for n in nodes if "Primary" in n.get("role", "")),
        nodes[0] if nodes else {"id": "p1", "name": "Primary Router", "role": "Primary Router", "position": {"x": 0, "y": 0}, "txPowerDbm": 21.0, "band": "5 GHz"},
    )

    # Strategy 1: Relocate Primary Router to RF Centroid
    best_pos = primary["position"]
    best_score = -1e9
    for cand in eval_points:
        test_primary = dict(primary, position=cand)
        g, d = coverage_stats([test_primary])
        score = g - 1.5 * d
        if score > best_score:
            best_score = score
            best_pos = cand

    relocated_primary = dict(primary, position=best_pos)
    rel_good, rel_dead = coverage_stats([relocated_primary])

    # Strategy 2: Keep Primary Router Fixed + Add Optimal Mesh Node
    best_sat_pos = {"x": 1.6, "y": 0.0}
    best_mesh_score = -1e9
    for cand in eval_points:
        if math.hypot(cand["x"] - primary["position"]["x"], cand["y"] - primary["position"]["y"]) < 2.2:
            continue
        bh_rssi = predict_rssi(primary, cand, walls)
        bh_bonus = 18.0 if -66.0 <= bh_rssi <= -50.0 else (-45.0 if bh_rssi < -72.0 else 0.0)
        sat = {
            "id": "mesh-opt-1",
            "name": "Suggested Mesh Node #1",
            "role": "Suggested Mesh Node",
            "position": cand,
            "band": primary.get("band", "5 GHz"),
            "txPowerDbm": primary.get("txPowerDbm", 21.0),
            "backhaul": "Wireless Mesh Backhaul",
        }
        g, d = coverage_stats([primary, sat])
        score = g - 1.5 * d + bh_bonus
        if score > best_mesh_score:
            best_mesh_score = score
            best_sat_pos = cand

    opt_sat = {
        "id": "mesh-opt-1",
        "name": "Suggested Mesh Node #1",
        "role": "Suggested Mesh Node",
        "position": best_sat_pos,
        "band": primary.get("band", "5 GHz"),
        "txPowerDbm": primary.get("txPowerDbm", 21.0),
        "backhaul": "Wireless Mesh Backhaul",
    }
    mesh_good, mesh_dead = coverage_stats([primary, opt_sat])
    bh_dbm = int(round(predict_rssi(primary, best_sat_pos, walls)))

    return [
        {
            "id": "rec-mesh",
            "title": "Keep Router at ISP Jack + Add 1 Mesh Node",
            "summary": f"Place a Mesh Node at ({best_sat_pos['x']}m, {best_sat_pos['y']}m) with {bh_dbm} dBm wireless backhaul to boost good coverage from {base_good}% to {mesh_good}%.",
            "recommendedNodes": [primary, opt_sat],
            "currentGoodCoveragePercent": base_good,
            "predictedGoodCoveragePercent": mesh_good,
            "currentDeadZonePercent": base_dead,
            "predictedDeadZonePercent": mesh_dead,
            "meshBackhaulRssiDbm": bh_dbm,
        },
        {
            "id": "rec-relocate",
            "title": "Relocate Primary Router to Central Line-of-Sight",
            "summary": f"Move '{primary['name']}' to ({best_pos['x']}m, {best_pos['y']}m) to raise single-router coverage from {base_good}% to {rel_good}%.",
            "recommendedNodes": [relocated_primary],
            "currentGoodCoveragePercent": base_good,
            "predictedGoodCoveragePercent": rel_good,
            "currentDeadZonePercent": base_dead,
            "predictedDeadZonePercent": rel_dead,
            "meshBackhaulRssiDbm": None,
        },
    ]


CURRENT_PROJECT = default_demo_project()

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>WiFi Mapper — Companion LAN Server & Heatmap Studio</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0c0f17; color: #f1f5f9; }
  header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; padding: 14px 20px; background: #141926; border-bottom: 1px solid #262f45; gap: 12px; }
  .badge { background: #1e293b; padding: 6px 12px; border-radius: 999px; font-size: 13px; color: #38bdf8; font-family: monospace; }
  main { display: grid; grid-template-columns: 1fr 370px; gap: 16px; padding: 16px; max-width: 1440px; margin: 0 auto; }
  @media (max-width: 960px) { main { grid-template-columns: 1fr; } }
  .card { background: #141926; border: 1px solid #262f45; border-radius: 14px; padding: 16px; }
  canvas { width: 100%; height: 540px; background: #090c12; border-radius: 10px; cursor: crosshair; display: block; }
  .btn { background: #2563eb; color: white; border: none; padding: 9px 14px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px; }
  .btn.purple { background: #7c3aed; }
  .btn.green { background: #10b981; color: #052e16; }
  .rec-card { background: #1b2234; border: 1px solid #334155; border-radius: 10px; padding: 12px; margin-top: 10px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
</style>
</head>
<body>
<header>
  <div>
    <strong style="font-size:18px;">WiFi Mapper — Companion LAN Speed Server & Floorplan Visualizer</strong>
    <div style="font-size:12px;color:#94a3b8;margin-top:3px;">Enter this URL in your iPhone WiFi Mapper app or test directly in mobile Safari</div>
  </div>
  <div id="serverUrlBadge" class="badge">LAN Server Active</div>
</header>
<main>
  <div class="card">
    <div class="toolbar">
      <button class="btn" onclick="setMode('router')">+ Place Primary Router</button>
      <button class="btn" onclick="setMode('mesh')">+ Place Mesh Node</button>
      <button class="btn green" onclick="runBrowserSpeedProbe()">Run Live Wi-Fi Speed Sample Here</button>
      <button class="btn purple" onclick="resetDemo()">Reset 2-Bedroom Demo</button>
      <span id="statusText" style="font-size:12px;color:#94a3b8;align-self:center;margin-left:auto;">Click map to test signal or place nodes</span>
    </div>
    <canvas id="floorCanvas" width="900" height="540"></canvas>
  </div>
  <div class="card">
    <h3 style="margin-top:0;">Router & Mesh Placement Advisor</h3>
    <div id="recContainer"></div>
    <hr style="border-color:#262f45;margin:16px 0;"/>
    <h4>Live LAN Speed Probe</h4>
    <div id="probeResult" style="font-family:monospace;font-size:13px;color:#38bdf8;">Ready to measure local Wi-Fi link...</div>
  </div>
</main>
<script>
let project = null;
let clickMode = 'sample';
let previewNodes = null;

function setMode(m) {
  clickMode = m;
  document.getElementById('statusText').textContent = 'Click on floorplan to place ' + m.toUpperCase();
}

async function loadProject() {
  const res = await fetch('/api/project');
  project = await res.json();
  document.getElementById('serverUrlBadge').textContent = window.location.origin;
  renderRecommendations();
  drawMap();
}

async function resetDemo() {
  const res = await fetch('/api/reset', {method: 'POST'});
  project = await res.json();
  previewNodes = null;
  renderRecommendations();
  drawMap();
}

function previewRec(idx) {
  previewNodes = project.recommendations[idx].recommendedNodes;
  drawMap();
}

async function applyRec(idx) {
  project.nodes = project.recommendations[idx].recommendedNodes;
  previewNodes = null;
  const res = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(project)});
  project = await res.json();
  renderRecommendations();
  drawMap();
}

function renderRecommendations() {
  const container = document.getElementById('recContainer');
  container.innerHTML = '';
  (project.recommendations || []).forEach((rec, idx) => {
    const el = document.createElement('div');
    el.className = 'rec-card';
    el.innerHTML = `
      <div style="font-weight:700;color:#c084fc;margin-bottom:4px;">${rec.title}</div>
      <div style="font-size:12px;color:#cbd5e1;margin-bottom:8px;">${rec.summary}</div>
      <div style="font-size:12px;margin-bottom:8px;">
        <span style="color:#4ade80;font-weight:700;">Good Coverage: ${rec.currentGoodCoveragePercent}% → ${rec.predictedGoodCoveragePercent}%</span> |
        <span style="color:#fb923c;font-weight:700;">Dead Zones: ${rec.currentDeadZonePercent}% → ${rec.predictedDeadZonePercent}%</span>
      </div>
      <button class="btn purple" onclick="previewRec(${idx})">Preview Heatmap</button>
      <button class="btn green" onclick="applyRec(${idx})" style="margin-left:6px;">Apply</button>
    `;
    container.appendChild(el);
  });
}

async function runBrowserSpeedProbe() {
  const out = document.getElementById('probeResult');
  out.textContent = 'Probing LAN RTT & 4 MB Wi-Fi Burst...';
  const t0 = performance.now();
  await fetch('/api/ping');
  const rtt = performance.now() - t0;

  const td0 = performance.now();
  const downRes = await fetch('/api/download?bytes=4000000');
  const buf = await downRes.arrayBuffer();
  const dt = Math.max(0.005, (performance.now() - td0) / 1000.0);
  const mbps = (buf.byteLength * 8.0) / (dt * 1e6);
  out.textContent = `LAN Download: ${mbps.toFixed(1)} Mbps | Gateway RTT: ${rtt.toFixed(1)} ms`;
}

function rssiColor(rssi) {
  const t = Math.max(0, Math.min(1, (rssi - (-88)) / (-40 - (-88))));
  const r = t < 0.5 ? 240 : Math.round(240 * (1 - (t - 0.5) * 2));
  const g = t < 0.5 ? Math.round(60 + 180 * (t * 2)) : 230;
  const b = t > 0.8 ? 220 : 40;
  return `rgba(${r},${g},${b},0.62)`;
}

function drawMap() {
  if (!project) return;
  const canvas = document.getElementById('floorCanvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const ppm = 54.0;
  const activeNodes = previewNodes || project.nodes || [];

  // Heatmap inside house (-6..6m, -4..4m)
  for (let gx = -5.8; gx <= 5.8; gx += 0.35) {
    for (let gy = -3.8; gy <= 3.8; gy += 0.35) {
      let bestRssi = -92;
      activeNodes.forEach(n => {
        const d = Math.max(0.6, Math.hypot(n.position.x - gx, n.position.y - gy));
        let rssi = 21.0 - 46.5 - 31.0 * Math.log10(d);
        (project.walls || []).forEach(w => {
          if (!w.isDoorway && segIntersect(n.position, {x:gx,y:gy}, w.start, w.end)) {
            rssi -= (w.material.includes('Brick') ? 8.5 : 4.0);
          }
        });
        if (rssi > bestRssi) bestRssi = rssi;
      });
      const sx = cx + gx * ppm;
      const sy = cy - gy * ppm;
      ctx.fillStyle = rssiColor(bestRssi);
      ctx.fillRect(sx - 10, sy - 10, 20, 20);
    }
  }

  // Walls
  (project.walls || []).forEach(w => {
    ctx.beginPath();
    ctx.moveTo(cx + w.start.x * ppm, cy - w.start.y * ppm);
    ctx.lineTo(cx + w.end.x * ppm, cy - w.end.y * ppm);
    ctx.strokeStyle = w.isDoorway ? '#38bdf8' : '#ffffff';
    ctx.lineWidth = w.isDoorway ? 2 : 4;
    ctx.setLineDash(w.isDoorway ? [6, 4] : []);
    ctx.stroke();
  });
  ctx.setLineDash([]);

  // Room labels
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.font = 'bold 12px sans-serif';
  ctx.textAlign = 'center';
  (project.roomLabels || []).forEach(r => {
    ctx.fillText(r.name, cx + r.position.x * ppm, cy - r.position.y * ppm);
  });

  // Nodes
  activeNodes.forEach(n => {
    const sx = cx + n.position.x * ppm;
    const sy = cy - n.position.y * ppm;
    ctx.beginPath();
    ctx.arc(sx, sy, 10, 0, Math.PI * 2);
    ctx.fillStyle = n.role.includes('Primary') ? '#2563eb' : '#a855f7';
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.fillText(n.name, sx, sy + 22);
  });
}

function segIntersect(p1, p2, p3, p4) {
  function ccw(A, B, C) { return (C.y - A.y) * (B.x - A.x) - (B.y - A.y) * (C.x - A.x); }
  const d1 = ccw(p3, p4, p1), d2 = ccw(p3, p4, p2), d3 = ccw(p1, p2, p3), d4 = ccw(p1, p2, p4);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

document.getElementById('floorCanvas').addEventListener('click', async (e) => {
  if (!project) return;
  const rect = e.target.getBoundingClientRect();
  const scaleX = e.target.width / rect.width;
  const scaleY = e.target.height / rect.height;
  const sx = (e.clientX - rect.left) * scaleX;
  const sy = (e.clientY - rect.top) * scaleY;
  const ppm = 54.0;
  const wx = +((sx - e.target.width / 2) / ppm).toFixed(2);
  const wy = +((e.target.height / 2 - sy) / ppm).toFixed(2);

  if (clickMode === 'router') {
    project.nodes = [{id: 'r-' + Date.now(), name: 'Primary Router', role: 'Primary Router', position: {x:wx, y:wy}, band: '5 GHz', txPowerDbm: 21.0, backhaul: 'Wired Ethernet Backhaul'}];
  } else if (clickMode === 'mesh') {
    project.nodes.push({id: 'm-' + Date.now(), name: 'Mesh Node #' + project.nodes.length, role: 'Mesh Satellite / Extender', position: {x:wx, y:wy}, band: '5 GHz', txPowerDbm: 21.0, backhaul: 'Wireless Mesh Backhaul'});
  }
  previewNodes = null;
  clickMode = 'sample';
  const res = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(project)});
  project = await res.json();
  renderRecommendations();
  drawMap();
});

loadProject();
</script>
</body>
</html>
"""


class CompanionHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return  # Keep console clean during high-frequency speed bursts

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ping":
            self._send_json({"ok": True, "ts": time.time()})
            return

        if parsed.path == "/api/download":
            qs = parse_qs(parsed.query)
            requested = int(qs.get("bytes", ["2000000"])[0])
            total_bytes = max(1024, min(requested, 25_000_000))
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(total_bytes))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            remaining = total_bytes
            while remaining > 0:
                chunk_len = min(remaining, len(SPEED_CHUNK))
                self.wfile.write(SPEED_CHUNK[:chunk_len])
                remaining -= chunk_len
            return

        if parsed.path == "/api/project":
            self._send_json(CURRENT_PROJECT)
            return

        # Serve interactive HTML studio
        html_bytes = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self) -> None:
        global CURRENT_PROJECT
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""

        if parsed.path == "/api/upload":
            self._send_json({"receivedBytes": len(raw), "ok": True})
            return

        if parsed.path == "/api/reset":
            CURRENT_PROJECT = default_demo_project()
            self._send_json(CURRENT_PROJECT)
            return

        if parsed.path in ("/api/sync", "/api/optimize"):
            try:
                incoming = json.loads(raw.decode("utf-8"))
                incoming["recommendations"] = compute_recommendations(incoming)
                CURRENT_PROJECT = incoming
                self._send_json(CURRENT_PROJECT)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "Not found"}, status=404)


def run_server(port: int = 8765) -> None:
    lan_ip = get_local_lan_ip()
    server = ThreadingHTTPServer(("0.0.0.0", port), CompanionHandler)
    print(f"WiFi Mapper Companion LAN Server running on:")
    print(f"  Local PC:   http://localhost:{port}")
    print(f"  iPhone URL: http://{lan_ip}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WiFi Mapper Companion LAN Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    args = parser.parse_args()
    run_server(args.port)

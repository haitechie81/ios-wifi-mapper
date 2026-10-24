#!/usr/bin/env python3
"""
WiFi Mapper — Companion LAN Speed Test Server & Automatic Camera CV + IMU + GPS vSLAM iOS App
Runs on Windows/macOS/Linux with zero third-party dependencies (Python 3.10+ stdlib + OpenSSL).

Serves:
  - HTTP  on port 8765 (e.g. http://10.0.5.106:8765)
  - HTTPS on port 8443 (e.g. https://10.0.5.106:8443) + Cloudflare Tunnel
"""

from __future__ import annotations
import argparse
import json
import math
import os
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SPEED_CHUNK = b"W" * (1024 * 1024)


def get_local_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def ensure_self_signed_cert(cert_dir: Path, lan_ip: str) -> tuple[Path, Path] | None:
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    try:
        cnf_path = cert_dir / "openssl_local.cnf"
        cnf_path.write_text(
            f"[req]\ndistinguished_name=dn\nx509_extensions=v3_req\nprompt=no\n"
            f"[dn]\nCN={lan_ip}\n"
            f"[v3_req]\nsubjectAltName=IP:{lan_ip},IP:10.0.5.106,IP:10.0.4.120,IP:100.110.63.72,IP:127.0.0.1,DNS:localhost\n",
            encoding="utf-8",
        )
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365", "-nodes",
            "-config", str(cnf_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cnf_path.unlink(missing_ok=True)
        return cert_path, key_path
    except Exception:
        return None


def default_demo_project() -> dict:
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
    free_cells = project.get("freeCells", [])

    if free_cells and len(free_cells) >= 6:
        step = max(1, len(free_cells) // 60)
        eval_points = [free_cells[i] for i in range(0, len(free_cells), step)]
    else:
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

    best_sat_pos = {"x": round(best_pos["x"] + 1.5, 2), "y": round(best_pos["y"], 2)}
    best_mesh_score = -1e9
    for cand in eval_points:
        if math.hypot(cand["x"] - primary["position"]["x"], cand["y"] - primary["position"]["y"]) < 2.0:
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
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="WiFi Mapper"/>
<title>WiFi Mapper — Automatic Camera vSLAM + Wi-Fi Heatmap</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #080b11; color: #f1f5f9; overflow-x: hidden; }
  .telemetry-bar { display: flex; gap: 8px; overflow-x: auto; padding: 8px 10px; background: #101521; border-bottom: 1px solid #1e293b; }
  .pill { background: #182032; padding: 6px 10px; border-radius: 10px; min-width: 102px; flex-shrink: 0; }
  .pill .lbl { font-size: 9px; font-weight: 700; color: #94a3b8; letter-spacing: 0.04em; }
  .pill .val { font-size: 13px; font-weight: 800; font-family: monospace; margin-top: 2px; }
  .pill .sub { font-size: 10px; color: #cbd5e1; }
  main { display: grid; grid-template-columns: 1fr 360px; gap: 10px; padding: 10px; max-width: 1440px; margin: 0 auto; }
  @media (max-width: 960px) { main { grid-template-columns: 1fr; padding: 6px; } }
  .card { background: #121723; border: 1px solid #232e45; border-radius: 14px; padding: 10px; }
  .camera-drawer { position: relative; width: 100%; height: 210px; background: #05070b; border-radius: 10px; overflow: hidden; margin-bottom: 8px; border: 1px solid #334155; }
  video#camVideo { width: 100%; height: 100%; object-fit: cover; display: block; }
  canvas#visionOverlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .cam-hud { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; justify-content: space-between; align-items: center; background: rgba(8,11,17,0.76); padding: 5px 10px; border-radius: 999px; font-size: 11px; font-family: monospace; backdrop-filter: blur(6px); }
  canvas#floorCanvas { width: 100%; height: 420px; background: #06090e; border-radius: 10px; display: block; touch-action: none; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .btn { background: #2563eb; color: white; border: none; padding: 9px 13px; border-radius: 9px; font-weight: 700; cursor: pointer; font-size: 12px; }
  .btn.purple { background: #7c3aed; }
  .btn.green { background: #10b981; color: #042f1e; }
  .btn.red { background: #ef4444; color: #fff; }
  .btn.slate { background: #334155; color: #e2e8f0; }
  .rec-card { background: #182032; border: 1px solid #2d3b54; border-radius: 10px; padding: 11px; margin-top: 8px; }
</style>
</head>
<body>
<div class="telemetry-bar">
  <div class="pill">
    <div class="lbl">SIGNAL (RSSI)</div>
    <div id="uiRssi" class="val" style="color:#4ade80;">-- dBm</div>
    <div id="uiTier" class="sub">Ready</div>
  </div>
  <div class="pill">
    <div class="lbl">THROUGHPUT</div>
    <div id="uiMbps" class="val" style="color:#38bdf8;">-- Mbps</div>
    <div id="uiUpMbps" class="sub">Auto-Probing</div>
  </div>
  <div class="pill">
    <div class="lbl">PING / JITTER</div>
    <div id="uiPing" class="val" style="color:#4ade80;">-- ms</div>
    <div id="uiJitter" class="sub">±-- ms</div>
  </div>
  <div class="pill">
    <div class="lbl">MAPPED AREA</div>
    <div id="uiArea" class="val" style="color:#c084fc;">0 m²</div>
    <div id="uiWallsCount" class="sub">0 auto-walls</div>
  </div>
  <div class="pill">
    <div class="lbl">POSE & GPS</div>
    <div id="uiPose" class="val" style="color:#60a5fa;">0.0m, 0.0m</div>
    <div id="uiGps" class="sub">Compass 0°</div>
  </div>
</div>

<main>
  <div class="card">
    <div class="toolbar">
      <button id="btnScan" class="btn green" style="flex:1;font-size:13px;" onclick="toggleAutoMapping()">
        ▶ Start Automatic House Mapping (Just Walk Around)
      </button>
      <button class="btn" onclick="dropNodeHere('Primary Router')">📍 Drop My Router Here</button>
      <button class="btn purple" onclick="dropNodeHere('Mesh Satellite')">📍 Drop Mesh Here</button>
      <button class="btn slate" onclick="resetBlankAutoMap()">Clear Map</button>
      <button class="btn slate" onclick="resetDemo()">Demo House</button>
    </div>

    <!-- Live Camera + Real-Time Computer Vision Wall Horizon Overlay -->
    <div id="camDrawer" class="camera-drawer">
      <video id="camVideo" autoplay playsinline muted></video>
      <canvas id="visionOverlay" width="320" height="180"></canvas>
      <div class="cam-hud">
        <span id="camStatus" style="color:#4ade80;">● Tap "Start Automatic House Mapping" above & walk through your house</span>
        <span id="cvFps" style="color:#38bdf8;">vSLAM Standby</span>
      </div>
    </div>

    <!-- 2D Robot-Vacuum Occupancy Grid + Color-Coded Wi-Fi Heatmap -->
    <canvas id="floorCanvas" width="900" height="460"></canvas>
    <div id="statusText" style="font-size:11px;color:#94a3b8;margin-top:6px;">
      Hold your iPhone naturally facing forward as you walk. Camera vision automatically detects floor-to-wall boundaries, carves rooms, and maps Wi-Fi signal & speed.
    </div>
  </div>

  <div class="card">
    <h3 style="margin:0 0 4px 0;font-size:15px;">Automatic Router & Mesh Placement Advisor</h3>
    <div style="font-size:11px;color:#94a3b8;margin-bottom:8px;">
      Continuously analyzes your mapped walls, rooms, and dead zones to suggest optimal router & mesh node placement.
    </div>
    <div id="recContainer"></div>
  </div>
</main>

<script>
// ============================================================================
// AUTOMATIC CAMERA CV + IMU + GPS + WI-FI vSLAM ENGINE (ROBOT-VACUUM STYLE)
// ============================================================================
const CELL_SIZE = 0.25; // 25cm occupancy grid cells
let occupancyLogOdds = new Map(); // key "gx,gy" -> logOdds (-4.0 free .. +4.0 wall)
let cellStates = new Map();       // key "gx,gy" -> 1 (free) | 2 (wall)

let project = null;
let previewNodes = null;
let isScanning = false;
let userPos = { x: 0.0, y: 0.0 };
let userHeadingDeg = 0.0;
let userPitchDeg = 65.0; // Phone held at ~65 deg tilt (0=flat on table, 90=vertical)
let originGps = null;
let lastGpsPos = null;
let lastSampledPos = null;
let lastStepTime = 0;
let rttHistory = [];

// Offscreen Computer Vision canvas (160x120) for 10Hz Floor-to-Wall Raycast & Optical Flow
const cvCanvas = document.createElement('canvas');
cvCanvas.width = 160;
cvCanvas.height = 120;
const cvCtx = cvCanvas.getContext('2d', { willReadFrequently: true });
let prevGrayFrame = null;
let cvLoopTimer = null;
let wifiLoopTimer = null;
let syncLoopTimer = null;

function cellKey(gx, gy) { return `${gx},${gy}`; }
function parseKey(k) { const p = k.split(','); return { gx: parseInt(p[0], 10), gy: parseInt(p[1], 10) }; }
function worldToGrid(x, y) { return { gx: Math.floor(x / CELL_SIZE), gy: Math.floor(y / CELL_SIZE) }; }
function gridToWorld(gx, gy) { return { x: (gx + 0.5) * CELL_SIZE, y: (gy + 0.5) * CELL_SIZE }; }

function updateOccupancyCell(gx, gy, delta) {
  const k = cellKey(gx, gy);
  const cur = (occupancyLogOdds.get(k) || 0.0) + delta;
  const clamped = Math.max(-4.5, Math.min(4.5, cur));
  occupancyLogOdds.set(k, clamped);
  if (clamped >= 0.90) {
    cellStates.set(k, 2); // Wall obstacle
  } else if (clamped <= -0.30) {
    cellStates.set(k, 1); // Free interior floor
  }
}

function bresenhamLine(x0, y0, x1, y1) {
  const pts = [];
  let dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
  let sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy, steps = 0;
  while (steps++ < 150) {
    pts.push({ gx: x0, gy: y0 });
    if (x0 === x1 && y0 === y1) break;
    let e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
  return pts;
}

function markWalkedFootprint(pos, radiusMeters = 0.85) {
  const center = worldToGrid(pos.x, pos.y);
  const rCells = Math.ceil(radiusMeters / CELL_SIZE);
  for (let dx = -rCells; dx <= rCells; dx++) {
    for (let dy = -rCells; dy <= rCells; dy++) {
      const w = gridToWorld(center.gx + dx, center.gy + dy);
      if (Math.hypot(w.x - pos.x, w.y - pos.y) <= radiusMeters) {
        updateOccupancyCell(center.gx + dx, center.gy + dy, -0.95);
      }
    }
  }
}

function integrateVisionRay(originPos, hitPos, isWallHit) {
  const c0 = worldToGrid(originPos.x, originPos.y);
  const c1 = worldToGrid(hitPos.x, hitPos.y);
  const ray = bresenhamLine(c0.gx, c0.gy, c1.gx, c1.gy);
  for (let i = 0; i < ray.length; i++) {
    if (i === ray.length - 1 && isWallHit) {
      updateOccupancyCell(ray[i].gx, ray[i].gy, +0.75);
    } else {
      updateOccupancyCell(ray[i].gx, ray[i].gy, -0.32);
    }
  }
}

// Automatically extracts clean architectural vector WallSegments from occupancy grid obstacle clusters
function autoExtractVectorWalls() {
  const wallCells = [];
  cellStates.forEach((st, k) => {
    if (st === 2) wallCells.push(parseKey(k));
  });
  if (wallCells.length < 3) return;

  const newWalls = [];
  // Group horizontal runs (same gy)
  const byRow = new Map();
  wallCells.forEach(c => {
    if (!byRow.has(c.gy)) byRow.set(c.gy, []);
    byRow.get(c.gy).push(c.gx);
  });
  byRow.forEach((xs, gy) => {
    xs.sort((a, b) => a - b);
    let start = xs[0], prev = xs[0];
    for (let i = 1; i < xs.length; i++) {
      if (xs[i] <= prev + 1) {
        prev = xs[i];
      } else {
        if (prev - start >= 2) {
          newWalls.push({
            id: `wh-${gy}-${start}`,
            start: { x: +(start * CELL_SIZE).toFixed(2), y: +((gy + 0.5) * CELL_SIZE).toFixed(2) },
            end:   { x: +((prev + 1) * CELL_SIZE).toFixed(2), y: +((gy + 0.5) * CELL_SIZE).toFixed(2) },
            material: 'Drywall / Plaster',
            isDoorway: false
          });
        }
        start = prev = xs[i];
      }
    }
    if (prev - start >= 2) {
      newWalls.push({
        id: `wh-${gy}-${start}`,
        start: { x: +(start * CELL_SIZE).toFixed(2), y: +((gy + 0.5) * CELL_SIZE).toFixed(2) },
        end:   { x: +((prev + 1) * CELL_SIZE).toFixed(2), y: +((gy + 0.5) * CELL_SIZE).toFixed(2) },
        material: 'Drywall / Plaster',
        isDoorway: false
      });
    }
  });

  // Group vertical runs (same gx)
  const byCol = new Map();
  wallCells.forEach(c => {
    if (!byCol.has(c.gx)) byCol.set(c.gx, []);
    byCol.get(c.gx).push(c.gy);
  });
  byCol.forEach((ys, gx) => {
    ys.sort((a, b) => a - b);
    let start = ys[0], prev = ys[0];
    for (let i = 1; i < ys.length; i++) {
      if (ys[i] <= prev + 1) {
        prev = ys[i];
      } else {
        if (prev - start >= 2) {
          newWalls.push({
            id: `wv-${gx}-${start}`,
            start: { x: +((gx + 0.5) * CELL_SIZE).toFixed(2), y: +(start * CELL_SIZE).toFixed(2) },
            end:   { x: +((gx + 0.5) * CELL_SIZE).toFixed(2), y: +((prev + 1) * CELL_SIZE).toFixed(2) },
            material: 'Drywall / Plaster',
            isDoorway: false
          });
        }
        start = prev = ys[i];
      }
    }
    if (prev - start >= 2) {
      newWalls.push({
        id: `wv-${gx}-${start}`,
        start: { x: +((gx + 0.5) * CELL_SIZE).toFixed(2), y: +(start * CELL_SIZE).toFixed(2) },
        end:   { x: +((gx + 0.5) * CELL_SIZE).toFixed(2), y: +((prev + 1) * CELL_SIZE).toFixed(2) },
        material: 'Drywall / Plaster',
        isDoorway: false
      });
    }
  });

  project.walls = newWalls;
}

// Real-Time Camera Computer Vision Pipeline:
// 1. Optical Flow forward motion & rotation estimation
// 2. 11-Ray Floor-to-Wall Boundary Horizon Detection + Inverse Perspective Mapping
function processCameraVisionFrame() {
  if (!isScanning) return;
  const video = document.getElementById('camVideo');
  if (!video || video.readyState < 2 || !video.videoWidth) return;

  const W = cvCanvas.width;  // 160
  const H = cvCanvas.height; // 120
  cvCtx.drawImage(video, 0, 0, W, H);
  const imgData = cvCtx.getImageData(0, 0, W, H).data;

  // Build luminance & chromatic arrays
  const gray = new Float32Array(W * H);
  for (let i = 0, p = 0; i < imgData.length; i += 4, p++) {
    gray[p] = 0.299 * imgData[i] + 0.587 * imgData[i + 1] + 0.114 * imgData[i + 2];
  }

  // A. Optical Flow Motion Estimation (central patch vertical/horizontal shift)
  if (prevGrayFrame) {
    let bestDy = 0, bestSad = Infinity;
    for (let dy = -4; dy <= 4; dy++) {
      let sad = 0;
      for (let y = 55; y < 95; y += 2) {
        for (let x = 50; x < 110; x += 2) {
          sad += Math.abs(gray[y * W + x] - prevGrayFrame[(y + dy) * W + x]);
        }
      }
      if (sad < bestSad) { bestSad = sad; bestDy = dy; }
    }
    // Downward optical flow in lower field of view = user moving forward over floor texture
    if (bestDy >= 1 && (performance.now() - lastStepTime) > 350) {
      advanceUserByMeters(0.09 * bestDy);
    }
  }
  prevGrayFrame = gray;

  // B. Sample Floor Reference Color/Luminance near bottom center (ahead of feet)
  let floorR = 0, floorG = 0, floorB = 0, floorCount = 0;
  for (let y = H - 22; y < H - 4; y += 2) {
    for (let x = Math.floor(W * 0.35); x < Math.floor(W * 0.65); x += 2) {
      const idx = (y * W + x) * 4;
      floorR += imgData[idx];
      floorG += imgData[idx + 1];
      floorB += imgData[idx + 2];
      floorCount++;
    }
  }
  floorR /= Math.max(1, floorCount);
  floorG /= Math.max(1, floorCount);
  floorB /= Math.max(1, floorCount);

  // C. Cast 11 Visual Scan Rays across Camera Horizontal FOV (-32 deg to +32 deg)
  const overlay = document.getElementById('visionOverlay');
  const oCtx = overlay.getContext('2d');
  oCtx.clearRect(0, 0, overlay.width, overlay.height);
  const scaleX = overlay.width / W;
  const scaleY = overlay.height / H;

  const numRays = 11;
  const fovHorizDeg = 62.0;
  const fovVertDeg = 48.0;
  const camHeightMeters = 1.25;
  // Pitch depression below horizon (if phone held at 65 deg tilt, optical axis points 25 deg below horizontal)
  const opticalAxisDepressionDeg = Math.max(8.0, Math.min(42.0, 90.0 - userPitchDeg));

  const hitContourScreen = [];

  for (let r = 0; r < numRays; r++) {
    const frac = r / (numRays - 1); // 0 .. 1
    const colX = Math.floor(14 + frac * (W - 28));
    const rayAngleOffsetDeg = (frac - 0.5) * fovHorizDeg;

    let hitRowY = Math.floor(H * 0.25);
    let isStrongWallEdge = false;

    // March upward from bottom (floor near feet) to upper-middle (horizon)
    for (let y = H - 12; y >= Math.floor(H * 0.20); y--) {
      const pIdx = (y * W + colX) * 4;
      const dr = imgData[pIdx] - floorR;
      const dg = imgData[pIdx + 1] - floorG;
      const db = imgData[pIdx + 2] - floorB;
      const colorDist = Math.sqrt(dr * dr + dg * dg + db * db);

      // Vertical Sobel edge gradient (baseboard / floor-to-wall transition)
      const gradY = Math.abs(gray[(y - 2) * W + colX] - gray[(y + 2) * W + colX]);

      if ((gradY > 22 && colorDist > 28) || colorDist > 58) {
        hitRowY = y;
        isStrongWallEdge = true;
        break;
      }
    }

    // Convert hitRowY to metric distance using Pinhole Camera Inverse Perspective Mapping
    const pixelAngleFromCenterDeg = ((hitRowY - H * 0.5) / H) * fovVertDeg;
    const totalRayDepressionDeg = Math.max(14.0, opticalAxisDepressionDeg + pixelAngleFromCenterDeg);
    const rawDistMeters = camHeightMeters / Math.tan((totalRayDepressionDeg * Math.PI) / 180.0);
    const clampedDist = Math.max(0.65, Math.min(4.2, rawDistMeters));

    const worldRayHeadingRad = ((userHeadingDeg + rayAngleOffsetDeg) * Math.PI) / 180.0;
    const hitWorldPt = {
      x: userPos.x + Math.sin(worldRayHeadingRad) * clampedDist,
      y: userPos.y + Math.cos(worldRayHeadingRad) * clampedDist
    };

    integrateVisionRay(userPos, hitWorldPt, isStrongWallEdge && clampedDist < 3.6);

    const sx = colX * scaleX;
    const sy = hitRowY * scaleY;
    hitContourScreen.push({ sx, sy, isStrongWallEdge });

    // Draw green/cyan floor ray on camera HUD
    oCtx.beginPath();
    oCtx.moveTo(sx, overlay.height);
    oCtx.lineTo(sx, sy);
    oCtx.strokeStyle = isStrongWallEdge ? 'rgba(56, 189, 248, 0.55)' : 'rgba(74, 222, 128, 0.35)';
    oCtx.lineWidth = 2;
    oCtx.stroke();
  }

  // Draw Floor-to-Wall Boundary Horizon Line across camera view
  if (hitContourScreen.length > 1) {
    oCtx.beginPath();
    hitContourScreen.forEach((pt, idx) => {
      if (idx === 0) oCtx.moveTo(pt.sx, pt.sy);
      else oCtx.lineTo(pt.sx, pt.sy);
    });
    oCtx.strokeStyle = '#f43f5e';
    oCtx.lineWidth = 2.5;
    oCtx.stroke();

    hitContourScreen.forEach(pt => {
      oCtx.beginPath();
      oCtx.arc(pt.sx, pt.sy, 3.5, 0, Math.PI * 2);
      oCtx.fillStyle = pt.isStrongWallEdge ? '#f43f5e' : '#4ade80';
      oCtx.fill();
    });
  }

  autoExtractVectorWalls();
  updateMapMetricsUI();
  drawMap();
}

function updateMapMetricsUI() {
  let freeCount = 0;
  cellStates.forEach(st => { if (st === 1) freeCount++; });
  const areaSqM = freeCount * CELL_SIZE * CELL_SIZE;
  document.getElementById('uiArea').textContent = `${areaSqM.toFixed(0)} m²`;
  document.getElementById('uiWallsCount').textContent = `${(project.walls || []).length} auto-walls`;
  document.getElementById('cvFps').textContent = `vSLAM: ${(project.walls || []).length} walls`;
}

async function toggleAutoMapping() {
  const btn = document.getElementById('btnScan');
  if (isScanning) {
    isScanning = false;
    btn.textContent = '▶ Resume Automatic House Mapping';
    btn.className = 'btn green';
    if (cvLoopTimer) clearInterval(cvLoopTimer);
    if (wifiLoopTimer) clearInterval(wifiLoopTimer);
    if (syncLoopTimer) clearInterval(syncLoopTimer);
    const vid = document.getElementById('camVideo');
    if (vid.srcObject) {
      vid.srcObject.getTracks().forEach(t => t.stop());
      vid.srcObject = null;
    }
    await syncProjectToBackend();
    return;
  }

  // If currently displaying the preloaded Demo House, switch cleanly to live scanning map on first start
  if (project && project.name === "2-Bedroom Home Survey" && cellStates.size === 0) {
    await resetBlankAutoMap(false);
  }

  isScanning = true;
  btn.textContent = '⏸ Pause Auto-Mapping';
  btn.className = 'btn red';
  document.getElementById('camStatus').textContent = '● Auto-Detecting Walls & Mapping Wi-Fi...';

  // 1. Start iPhone Rear Camera Stream
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false
      });
      document.getElementById('camVideo').srcObject = stream;
    } catch (e) {
      document.getElementById('camStatus').textContent = '● IMU + GPS + Wi-Fi Auto-Mapping Active';
    }
  }

  // 2. Request iPhone Gyroscope/Compass & Accelerometer permissions
  if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
    try { await DeviceOrientationEvent.requestPermission(); } catch (e) {}
  }
  if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
    try { await DeviceMotionEvent.requestPermission(); } catch (e) {}
  }
  window.addEventListener('deviceorientation', onDeviceOrientation, true);
  window.addEventListener('devicemotion', onDeviceMotion, true);

  // 3. Start High-Accuracy GPS Tracking
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(onGpsUpdate, () => {}, {
      enableHighAccuracy: true,
      maximumAge: 1000,
      timeout: 5000
    });
  }

  // Mark initial standing footprint & capture first Wi-Fi sample
  markWalkedFootprint(userPos, 1.0);
  await probeAndRecordSampleAt(userPos);

  // 4. Run Camera Computer Vision Wall/Floor Scanner at 8 Hz
  cvLoopTimer = setInterval(processCameraVisionFrame, 125);

  // 5. Run Continuous Wi-Fi Speed & Signal Probing every 2.2s
  wifiLoopTimer = setInterval(() => {
    if (isScanning) probeAndRecordSampleAt(userPos);
  }, 2200);

  // 6. Auto-compute optimal router/mesh placement every 4s
  syncLoopTimer = setInterval(() => {
    if (isScanning) syncProjectToBackend();
  }, 4000);
}

function onDeviceOrientation(e) {
  let heading = e.webkitCompassHeading !== undefined ? e.webkitCompassHeading : (360 - (e.alpha || 0));
  userHeadingDeg = (heading + 360) % 360;
  if (e.beta !== null && e.beta !== undefined) {
    userPitchDeg = Math.max(15, Math.min(88, Math.abs(e.beta)));
  }
  document.getElementById('uiGps').textContent = `Compass ${Math.round(userHeadingDeg)}°`;
}

function onDeviceMotion(e) {
  if (!isScanning || !e.accelerationIncludingGravity) return;
  const a = e.accelerationIncludingGravity;
  const mag = Math.hypot(a.x || 0, a.y || 0, a.z || 0);
  const now = performance.now();
  // Automatic Footstep Detector
  if (mag > 11.3 && (now - lastStepTime) > 380) {
    lastStepTime = now;
    advanceUserByMeters(0.62);
  }
}

function onGpsUpdate(pos) {
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const acc = pos.coords.accuracy || 10.0;
  if (!originGps) {
    originGps = { lat, lon };
    lastGpsPos = { lat, lon };
    return;
  }
  // Convert GPS delta (lat/lon) to meters East (x) / North (y)
  const dNorth = (lat - originGps.lat) * 111320.0;
  const dEast = (lon - originGps.lon) * 111320.0 * Math.cos((originGps.lat * Math.PI) / 180.0);

  // Complementary filter: gently blend GPS position when accuracy <= 15m
  if (acc <= 15.0 && Math.hypot(dEast - userPos.x, dNorth - userPos.y) < 25.0) {
    const alpha = 0.15;
    userPos.x = +(userPos.x * (1 - alpha) + dEast * alpha).toFixed(2);
    userPos.y = +(userPos.y * (1 - alpha) + dNorth * alpha).toFixed(2);
    markWalkedFootprint(userPos, 0.85);
  }
}

function advanceUserByMeters(dist) {
  const rad = (userHeadingDeg * Math.PI) / 180.0;
  userPos = {
    x: +(userPos.x + Math.sin(rad) * dist).toFixed(2),
    y: +(userPos.y + Math.cos(rad) * dist).toFixed(2)
  };
  markWalkedFootprint(userPos, 0.85);
  if (!project.walkedTrajectory) project.walkedTrajectory = [];
  const lastPt = project.walkedTrajectory[project.walkedTrajectory.length - 1];
  if (!lastPt || Math.hypot(lastPt.x - userPos.x, lastPt.y - userPos.y) >= 0.20) {
    project.walkedTrajectory.push({ ...userPos });
  }
  document.getElementById('uiPose').textContent = `${userPos.x.toFixed(1)}m, ${userPos.y.toFixed(1)}m`;

  // Auto-sample Wi-Fi immediately if user walked >= 1.1m since last sample
  if (!lastSampledPos || Math.hypot(lastSampledPos.x - userPos.x, lastSampledPos.y - userPos.y) >= 1.1) {
    probeAndRecordSampleAt(userPos);
  }
  drawMap();
}

async function dropNodeHere(roleType) {
  if (!project.nodes) project.nodes = [];
  if (roleType.includes('Primary')) {
    project.nodes = project.nodes.filter(n => !n.role.includes('Primary'));
    project.nodes.unshift({
      id: 'router-primary',
      name: 'Primary Router',
      role: 'Primary Router',
      position: { ...userPos },
      heightMeters: 1.1,
      band: '5 GHz',
      txPowerDbm: 21.0,
      backhaul: 'Wired Ethernet Backhaul'
    });
  } else {
    const count = project.nodes.filter(n => !n.role.includes('Primary')).length + 1;
    project.nodes.push({
      id: 'mesh-' + Date.now(),
      name: `Mesh Node #${count}`,
      role: 'Mesh Satellite / Extender',
      position: { ...userPos },
      heightMeters: 1.1,
      band: '5 GHz',
      txPowerDbm: 21.0,
      backhaul: 'Wireless Mesh Backhaul'
    });
  }
  previewNodes = null;
  await syncProjectToBackend();
}

async function probeAndRecordSampleAt(pt) {
  lastSampledPos = { ...pt };
  try {
    const t0 = performance.now();
    await fetch('/api/ping?t=' + Date.now());
    const rtt = performance.now() - t0;
    rttHistory.push(rtt);
    if (rttHistory.length > 6) rttHistory.shift();
    const mean = rttHistory.reduce((a, b) => a + b, 0) / rttHistory.length;
    const jitter = Math.max(0.4, Math.sqrt(rttHistory.map(x => (x - mean) ** 2).reduce((a, b) => a + b, 0) / rttHistory.length));

    const td0 = performance.now();
    const downRes = await fetch('/api/download?bytes=1500000&t=' + Date.now());
    const buf = await downRes.arrayBuffer();
    const dt = Math.max(0.005, (performance.now() - td0) / 1000.0);
    const downMbps = (buf.byteLength * 8.0) / (dt * 1e6);

    const speedNorm = Math.min(1, Math.max(0, downMbps / 500.0));
    const latNorm = Math.max(0, 1.0 - (rtt - 3.0) / 75.0);
    const estRssi = Math.round(-85 + (0.65 * speedNorm + 0.35 * latNorm) * 43);

    const sample = {
      id: 's-' + Date.now(),
      position: { ...pt },
      rssiDbm: estRssi,
      isEstimatedRssi: true,
      downloadMbps: +downMbps.toFixed(1),
      uploadMbps: +(downMbps * 0.55).toFixed(1),
      latencyMs: +rtt.toFixed(1),
      jitterMs: +jitter.toFixed(1),
      packetLossPercent: 0.0
    };
    project.samples.push(sample);

    // If user hasn't manually pinned their Primary Router yet, automatically infer router position at strongest signal point!
    if (!project.nodes || project.nodes.length === 0) {
      project.nodes = [{
        id: 'auto-router',
        name: 'Primary Router (Auto-Detected Peak)',
        role: 'Primary Router',
        position: { ...pt },
        heightMeters: 1.1,
        band: '5 GHz',
        txPowerDbm: 21.0,
        backhaul: 'Wired Ethernet Backhaul'
      }];
    } else if (project.nodes[0].id === 'auto-router') {
      const bestSample = project.samples.reduce((best, cur) => (cur.rssiDbm > best.rssiDbm ? cur : best), project.samples[0]);
      project.nodes[0].position = { ...bestSample.position };
    }

    updateTelemetryUI(sample);
    drawMap();
  } catch (e) {}
}

async function syncProjectToBackend() {
  if (!project) return;
  const freeList = [];
  cellStates.forEach((st, k) => {
    if (st === 1) {
      const g = parseKey(k);
      freeList.push(gridToWorld(g.gx, g.gy));
    }
  });
  project.freeCells = freeList;
  try {
    const res = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(project)
    });
    project = await res.json();
    renderRecommendations();
    drawMap();
  } catch (e) {}
}

async function loadProject() {
  const res = await fetch('/api/project');
  project = await res.json();
  updateTelemetryUI();
  renderRecommendations();
  drawMap();
}

async function resetDemo() {
  occupancyLogOdds.clear();
  cellStates.clear();
  const res = await fetch('/api/reset', { method: 'POST' });
  project = await res.json();
  previewNodes = null;
  userPos = { x: 0.0, y: 0.0 };
  renderRecommendations();
  drawMap();
}

async function resetBlankAutoMap(syncNow = true) {
  occupancyLogOdds.clear();
  cellStates.clear();
  userPos = { x: 0.0, y: 0.0 };
  previewNodes = null;
  project = {
    name: "Auto-Scanned Home Map",
    walls: [],
    nodes: [],
    roomLabels: [],
    walkedTrajectory: [{ x: 0, y: 0 }],
    samples: [],
    recommendations: []
  };
  markWalkedFootprint(userPos, 0.9);
  if (syncNow) await syncProjectToBackend();
  drawMap();
}

function updateTelemetryUI(latest) {
  const s = latest || (project && project.samples && project.samples[project.samples.length - 1]);
  if (!s) return;
  document.getElementById('uiRssi').textContent = `${s.rssiDbm} dBm`;
  document.getElementById('uiTier').textContent = s.rssiDbm >= -55 ? 'Excellent' : (s.rssiDbm >= -67 ? 'Good' : (s.rssiDbm >= -75 ? 'Fair' : 'Dead Zone'));
  document.getElementById('uiMbps').textContent = `${s.downloadMbps.toFixed(0)} Mbps`;
  document.getElementById('uiUpMbps').textContent = `↑ ${s.uploadMbps.toFixed(0)} Mbps`;
  document.getElementById('uiPing').textContent = `${s.latencyMs.toFixed(1)} ms`;
  document.getElementById('uiJitter').textContent = `±${s.jitterMs.toFixed(1)} ms`;
}

function previewRec(idx) {
  previewNodes = project.recommendations[idx].recommendedNodes;
  drawMap();
}

async function applyRec(idx) {
  project.nodes = project.recommendations[idx].recommendedNodes;
  previewNodes = null;
  await syncProjectToBackend();
}

function renderRecommendations() {
  const container = document.getElementById('recContainer');
  container.innerHTML = '';
  (project.recommendations || []).forEach((rec, idx) => {
    const el = document.createElement('div');
    el.className = 'rec-card';
    el.innerHTML = `
      <div style="font-weight:700;color:#c084fc;margin-bottom:4px;font-size:13px;">${rec.title}</div>
      <div style="font-size:11px;color:#cbd5e1;margin-bottom:8px;">${rec.summary}</div>
      <div style="font-size:11px;margin-bottom:8px;">
        <span style="color:#4ade80;font-weight:700;">Good Coverage: ${rec.currentGoodCoveragePercent}% → ${rec.predictedGoodCoveragePercent}%</span><br/>
        <span style="color:#fb923c;font-weight:700;">Dead Zones: ${rec.currentDeadZonePercent}% → ${rec.predictedDeadZonePercent}%</span>
      </div>
      <button class="btn purple" onclick="previewRec(${idx})">Preview Optimal Map</button>
      <button class="btn green" onclick="applyRec(${idx})" style="margin-left:6px;">Apply</button>
    `;
    container.appendChild(el);
  });
}

function rssiColor(rssi) {
  const t = Math.max(0, Math.min(1, (rssi - (-88)) / (-40 - (-88))));
  const r = t < 0.5 ? 240 : Math.round(240 * (1 - (t - 0.5) * 2));
  const g = t < 0.5 ? Math.round(60 + 180 * (t * 2)) : 230;
  const b = t > 0.8 ? 220 : 40;
  return `rgba(${r},${g},${b},0.66)`;
}

function evaluateHeatmapRssiAt(wx, wy, activeNodes) {
  // Blend measured walked Wi-Fi samples (IDW) with Multi-Wall RF model
  if (!previewNodes && project.samples && project.samples.length > 0) {
    let wSum = 0, rSum = 0, minDist = 999;
    for (const s of project.samples) {
      const d = Math.hypot(s.position.x - wx, s.position.y - wy);
      if (d < minDist) minDist = d;
      if (d < 0.08) return s.rssiDbm;
      const w = 1.0 / ((d + 0.25) ** 2.1);
      wSum += w;
      rSum += s.rssiDbm * w;
    }
    const idwRssi = rSum / wSum;
    if (minDist <= 2.2 || activeNodes.length === 0) return idwRssi;
  }

  let bestRssi = -92;
  activeNodes.forEach(n => {
    const d = Math.max(0.6, Math.hypot(n.position.x - wx, n.position.y - wy));
    let rssi = 21.0 - 46.5 - 31.0 * Math.log10(d) - 5.5;
    (project.walls || []).forEach(w => {
      if (!w.isDoorway && segIntersect(n.position, { x: wx, y: wy }, w.start, w.end)) {
        rssi -= (w.material.includes('Brick') ? 8.5 : 4.0);
      }
    });
    if (rssi > bestRssi) bestRssi = rssi;
  });
  return bestRssi;
}

function drawMap() {
  if (!project) return;
  const canvas = document.getElementById('floorCanvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const ppm = 48.0;
  const activeNodes = previewNodes || project.nodes || [];

  // 1m Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  for (let x = cx % ppm; x < canvas.width; x += ppm) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
  }
  for (let y = cy % ppm; y < canvas.height; y += ppm) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }

  // Render Heatmap over Carved Occupancy Grid Cells (or Demo Floor Bounds)
  if (cellStates.size > 0) {
    const cellPx = CELL_SIZE * ppm + 1;
    cellStates.forEach((st, k) => {
      const g = parseKey(k);
      const w = gridToWorld(g.gx, g.gy);
      const sx = cx + w.x * ppm;
      const sy = cy - w.y * ppm;
      if (st === 1) {
        const rssi = evaluateHeatmapRssiAt(w.x, w.y, activeNodes);
        ctx.fillStyle = rssiColor(rssi);
        ctx.fillRect(sx - cellPx / 2, sy - cellPx / 2, cellPx, cellPx);
      } else if (st === 2) {
        ctx.fillStyle = 'rgba(248, 250, 252, 0.45)';
        ctx.fillRect(sx - cellPx / 2, sy - cellPx / 2, cellPx, cellPx);
      }
    });
  } else {
    for (let gx = -5.8; gx <= 5.8; gx += 0.35) {
      for (let gy = -3.8; gy <= 3.8; gy += 0.35) {
        const bestRssi = evaluateHeatmapRssiAt(gx, gy, activeNodes);
        const sx = cx + gx * ppm;
        const sy = cy - gy * ppm;
        ctx.fillStyle = rssiColor(bestRssi);
        ctx.fillRect(sx - 9, sy - 9, 18, 18);
      }
    }
  }

  // Walked Trajectory
  if (project.walkedTrajectory && project.walkedTrajectory.length > 1) {
    ctx.beginPath();
    project.walkedTrajectory.forEach((pt, i) => {
      const sx = cx + pt.x * ppm, sy = cy - pt.y * ppm;
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.strokeStyle = 'rgba(255,255,255,0.6)';
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Auto-Extracted & Architectural Walls
  (project.walls || []).forEach(w => {
    ctx.beginPath();
    ctx.moveTo(cx + w.start.x * ppm, cy - w.start.y * ppm);
    ctx.lineTo(cx + w.end.x * ppm, cy - w.end.y * ppm);
    ctx.strokeStyle = w.isDoorway ? '#38bdf8' : '#ffffff';
    ctx.lineWidth = w.isDoorway ? 2 : 3.5;
    ctx.setLineDash(w.isDoorway ? [6, 4] : []);
    ctx.stroke();
  });
  ctx.setLineDash([]);

  // Room labels
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.font = 'bold 11px sans-serif';
  ctx.textAlign = 'center';
  (project.roomLabels || []).forEach(r => {
    ctx.fillText(r.name, cx + r.position.x * ppm, cy - r.position.y * ppm);
  });

  // Samples
  (project.samples || []).forEach(s => {
    const sx = cx + s.position.x * ppm, sy = cy - s.position.y * ppm;
    ctx.beginPath();
    ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
  });

  // Nodes (Current + Suggested)
  activeNodes.forEach(n => {
    const sx = cx + n.position.x * ppm;
    const sy = cy - n.position.y * ppm;
    ctx.beginPath();
    ctx.arc(sx, sy, 10, 0, Math.PI * 2);
    ctx.fillStyle = n.role.includes('Primary') ? '#2563eb' : (n.role.includes('Suggested') ? '#10b981' : '#a855f7');
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.fillText(n.name, sx, sy + 21);
  });

  // Current User Pose + Live Camera FOV Cone
  const ux = cx + userPos.x * ppm;
  const uy = cy - userPos.y * ppm;
  const rad = ((userHeadingDeg - 90) * Math.PI) / 180.0;
  ctx.beginPath();
  ctx.moveTo(ux, uy);
  ctx.arc(ux, uy, 32, rad - 0.52, rad + 0.52);
  ctx.closePath();
  ctx.fillStyle = 'rgba(56, 189, 248, 0.35)';
  ctx.fill();

  ctx.beginPath();
  ctx.arc(ux, uy, 6, 0, Math.PI * 2);
  ctx.fillStyle = '#38bdf8';
  ctx.fill();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 2;
  ctx.stroke();
}

function segIntersect(p1, p2, p3, p4) {
  function ccw(A, B, C) { return (C.y - A.y) * (B.x - A.x) - (B.y - A.y) * (C.x - A.x); }
  const d1 = ccw(p3, p4, p1), d2 = ccw(p3, p4, p2), d3 = ccw(p1, p2, p3), d4 = ccw(p1, p2, p4);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

loadProject();
</script>
</body>
</html>
"""


class CompanionHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

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
            requested = int(qs.get("bytes", ["1500000"])[0])
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

        html_bytes = DASHBOARD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.send_header("Cache-Control", "no-store")
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


def run_server(port: int = 8765, https_port: int = 8443) -> None:
    lan_ip = get_local_lan_ip()
    cert_dir = Path(__file__).resolve().parent
    certs = ensure_self_signed_cert(cert_dir, lan_ip)

    if certs:
        cert_path, key_path = certs
        https_server = ThreadingHTTPServer(("0.0.0.0", https_port), CompanionHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        https_server.socket = ctx.wrap_socket(https_server.socket, server_side=True)
        threading.Thread(target=https_server.serve_forever, daemon=True).start()
        print(f"  iPhone HTTPS Web App (Camera+IMU): https://{lan_ip}:{https_port}")

    server = ThreadingHTTPServer(("0.0.0.0", port), CompanionHandler)
    print(f"  Local PC / HTTP URL:               http://{lan_ip}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WiFi Mapper Companion LAN Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--https-port", type=int, default=int(os.environ.get("HTTPS_PORT", "8443")))
    args = parser.parse_args()
    run_server(args.port, args.https_port)

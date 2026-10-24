#!/usr/bin/env python3
"""
WiFi Mapper — Companion LAN Speed Test Server & Full iPhone Camera/IMU/GPS Web App (PWA)
Runs on Windows/macOS/Linux with zero third-party dependencies (Python 3.10+ stdlib + OpenSSL).

Serves:
  - HTTP  on port 8765 (e.g. http://10.0.4.120:8765)
  - HTTPS on port 8443 (e.g. https://10.0.4.120:8443) -> Unlocks iPhone Rear Camera,
    Gyroscope/Compass Heading, Accelerometer Step Odometry, and GPS in iOS Safari without Sideloadly!
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
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365", "-nodes",
            "-subj", f"/CN={lan_ip}",
            "-addext", f"subjectAltName=IP:{lan_ip},IP:127.0.0.1,DNS:localhost",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>
<meta name="apple-mobile-web-app-title" content="WiFi Mapper"/>
<title>WiFi Mapper — iPhone Camera + IMU + RF Heatmap App</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #090c12; color: #f1f5f9; overflow-x: hidden; }
  .telemetry-bar { display: flex; gap: 8px; overflow-x: auto; padding: 8px 12px; background: #111622; border-bottom: 1px solid #1e293b; }
  .pill { background: #1a2234; padding: 6px 10px; border-radius: 10px; min-width: 105px; flex-shrink: 0; }
  .pill .lbl { font-size: 9px; font-weight: 700; color: #94a3b8; letter-spacing: 0.04em; }
  .pill .val { font-size: 14px; font-weight: 800; font-family: monospace; margin-top: 2px; }
  .pill .sub { font-size: 10px; color: #cbd5e1; }
  main { display: grid; grid-template-columns: 1fr 370px; gap: 12px; padding: 12px; max-width: 1440px; margin: 0 auto; }
  @media (max-width: 960px) { main { grid-template-columns: 1fr; padding: 8px; } }
  .card { background: #131824; border: 1px solid #242f46; border-radius: 14px; padding: 12px; }
  .camera-drawer { position: relative; width: 100%; height: 220px; background: #05070b; border-radius: 10px; overflow: hidden; margin-bottom: 10px; display: none; border: 1px solid #334155; }
  .camera-drawer.active { display: block; }
  video#camVideo { width: 100%; height: 100%; object-fit: cover; }
  .cam-hud { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.65); padding: 6px 12px; border-radius: 999px; font-size: 11px; font-family: monospace; }
  canvas#floorCanvas { width: 100%; height: 480px; background: #070a0f; border-radius: 10px; cursor: crosshair; display: block; touch-action: none; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .btn { background: #2563eb; color: white; border: none; padding: 8px 12px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 12px; }
  .btn.active { outline: 2px solid #38bdf8; }
  .btn.purple { background: #7c3aed; }
  .btn.green { background: #10b981; color: #052e16; }
  .btn.red { background: #ef4444; color: #fff; }
  .btn.orange { background: #f59e0b; color: #000; }
  .rec-card { background: #192132; border: 1px solid #2e3b52; border-radius: 10px; padding: 11px; margin-top: 9px; }
</style>
</head>
<body>
<div class="telemetry-bar">
  <div class="pill">
    <div class="lbl">SIGNAL (EST)</div>
    <div id="uiRssi" class="val" style="color:#4ade80;">-56 dBm</div>
    <div id="uiTier" class="sub">Excellent</div>
  </div>
  <div class="pill">
    <div class="lbl">LAN THROUGHPUT</div>
    <div id="uiMbps" class="val" style="color:#38bdf8;">-- Mbps</div>
    <div id="uiUpMbps" class="sub">Tap Start Scan</div>
  </div>
  <div class="pill">
    <div class="lbl">PING / JITTER</div>
    <div id="uiPing" class="val" style="color:#4ade80;">-- ms</div>
    <div id="uiJitter" class="sub">±-- ms</div>
  </div>
  <div class="pill">
    <div class="lbl">POSE & HEADING</div>
    <div id="uiPose" class="val" style="color:#c084fc;">0.0m, 0.0m</div>
    <div id="uiHeading" class="sub">Compass: 0°</div>
  </div>
  <div class="pill">
    <div class="lbl">GPS ANCHOR</div>
    <div id="uiGps" class="val" style="color:#60a5fa;">Standby</div>
    <div id="uiSamplesCount" class="sub">13 samples</div>
  </div>
</div>

<main>
  <div class="card">
    <div class="toolbar">
      <button id="btnScan" class="btn green" onclick="toggleLiveWalkScan()">Start Camera + IMU House Scan</button>
      <button class="btn" onclick="stepForwardManual()">Walk Step (+0.7m)</button>
      <button class="btn" onclick="setMode('router')">+ Place Router</button>
      <button class="btn" onclick="setMode('mesh')">+ Place Mesh</button>
      <button class="btn orange" onclick="setMode('wall')">+ Draw Wall</button>
      <button class="btn purple" onclick="resetDemo()">Load 2-Bed Demo</button>
      <button class="btn" style="background:#334155;" onclick="clearBlankMap()">Start Blank Map</button>
    </div>

    <div id="camDrawer" class="camera-drawer">
      <video id="camVideo" autoplay playsinline muted></video>
      <div class="cam-hud">
        <span id="camStatus" style="color:#4ade80;">● Camera + IMU Step Odometry Active</span>
        <button class="btn orange" style="padding:4px 9px;font-size:11px;" onclick="pinWallInFront()">Pin Wall 1.5m Ahead</button>
      </div>
    </div>

    <canvas id="floorCanvas" width="900" height="500"></canvas>
    <div id="statusText" style="font-size:12px;color:#94a3b8;margin-top:8px;">
      Tip: On iPhone Safari, open <b>https://10.0.4.120:8443</b> and tap <b>Share → Add to Home Screen</b> for full-screen Camera + Gyro/Step mapping!
    </div>
  </div>

  <div class="card">
    <h3 style="margin:0 0 6px 0;font-size:16px;">Router & Mesh Placement Optimizer</h3>
    <div style="font-size:12px;color:#94a3b8;margin-bottom:8px;">Multi-Wall COST-231 RF model + wireless backhaul analyzer</div>
    <div id="recContainer"></div>
  </div>
</main>

<script>
let project = null;
let clickMode = 'sample';
let previewNodes = null;
let pendingWallStart = null;
let isScanning = false;
let scanTimer = null;
let userPos = { x: 0.0, y: 0.0 };
let userHeadingDeg = 0.0;
let lastStepTime = 0;
let rttHistory = [];

function setMode(m) {
  clickMode = m;
  pendingWallStart = null;
  document.getElementById('statusText').textContent = 'Tap on map to ' + m.toUpperCase();
}

async function loadProject() {
  const res = await fetch('/api/project');
  project = await res.json();
  updateTelemetryUI();
  renderRecommendations();
  drawMap();
}

async function resetDemo() {
  const res = await fetch('/api/reset', {method: 'POST'});
  project = await res.json();
  previewNodes = null;
  userPos = { x: 0.0, y: 0.0 };
  renderRecommendations();
  drawMap();
}

async function clearBlankMap() {
  project = {
    name: "My Scanned Home",
    walls: [],
    nodes: [{id: 'router-1', name: 'Primary Router', role: 'Primary Router', position: {x: 0, y: 0}, heightMeters: 1.1, band: '5 GHz', txPowerDbm: 21.0, backhaul: 'Wired Ethernet Backhaul'}],
    roomLabels: [],
    walkedTrajectory: [{x: 0, y: 0}],
    samples: []
  };
  userPos = { x: 0, y: 0 };
  previewNodes = null;
  const res = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(project)});
  project = await res.json();
  renderRecommendations();
  drawMap();
}

async function toggleLiveWalkScan() {
  const btn = document.getElementById('btnScan');
  const drawer = document.getElementById('camDrawer');
  if (isScanning) {
    isScanning = false;
    btn.textContent = 'Start Camera + IMU House Scan';
    btn.className = 'btn green';
    drawer.classList.remove('active');
    if (scanTimer) clearInterval(scanTimer);
    const vid = document.getElementById('camVideo');
    if (vid.srcObject) {
      vid.srcObject.getTracks().forEach(t => t.stop());
      vid.srcObject = null;
    }
    return;
  }

  isScanning = true;
  btn.textContent = 'Pause House Scan';
  btn.className = 'btn red';
  drawer.classList.add('active');

  // 1. Request iPhone Camera (Environment / Rear Camera)
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
      document.getElementById('camVideo').srcObject = stream;
    } catch (e) {
      document.getElementById('camStatus').textContent = '● IMU + Wi-Fi Scan Active (Use HTTPS :8443 for Camera)';
    }
  }

  // 2. Request iPhone iOS 13+ Gyroscope/Compass & Accelerometer permissions
  if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
    try { await DeviceOrientationEvent.requestPermission(); } catch (e) {}
  }
  if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
    try { await DeviceMotionEvent.requestPermission(); } catch (e) {}
  }

  window.addEventListener('deviceorientation', onDeviceOrientation, true);
  window.addEventListener('devicemotion', onDeviceMotion, true);

  // 3. Request GPS Anchor
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(pos => {
      document.getElementById('uiGps').textContent = `${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`;
    }, () => {}, { enableHighAccuracy: true });
  }

  // 4. Continuous LAN Wi-Fi Speed & Latency Probing every 2.5 seconds
  await probeAndRecordSampleAt(userPos);
  scanTimer = setInterval(() => {
    if (isScanning) probeAndRecordSampleAt(userPos);
  }, 2500);
}

function onDeviceOrientation(e) {
  let heading = e.webkitCompassHeading !== undefined ? e.webkitCompassHeading : (360 - (e.alpha || 0));
  userHeadingDeg = (heading + 360) % 360;
  document.getElementById('uiHeading').textContent = `Compass: ${Math.round(userHeadingDeg)}°`;
  drawMap();
}

function onDeviceMotion(e) {
  if (!isScanning || !e.accelerationIncludingGravity) return;
  const a = e.accelerationIncludingGravity;
  const mag = Math.hypot(a.x || 0, a.y || 0, a.z || 0);
  const now = performance.now();
  // Footstep peak detector (~11.8 m/s^2 threshold with 420ms debounce)
  if (mag > 11.8 && (now - lastStepTime) > 420) {
    lastStepTime = now;
    advanceUserByMeters(0.65);
  }
}

function stepForwardManual() {
  advanceUserByMeters(0.70);
  probeAndRecordSampleAt(userPos);
}

function advanceUserByMeters(dist) {
  const rad = (userHeadingDeg * Math.PI) / 180.0;
  userPos = {
    x: +(userPos.x + Math.sin(rad) * dist).toFixed(2),
    y: +(userPos.y + Math.cos(rad) * dist).toFixed(2)
  };
  if (!project.walkedTrajectory) project.walkedTrajectory = [];
  project.walkedTrajectory.push({...userPos});
  document.getElementById('uiPose').textContent = `${userPos.x.toFixed(1)}m, ${userPos.y.toFixed(1)}m`;
  drawMap();
}

async function pinWallInFront() {
  const rad = (userHeadingDeg * Math.PI) / 180.0;
  const cx = userPos.x + Math.sin(rad) * 1.5;
  const cy = userPos.y + Math.cos(rad) * 1.5;
  const perpX = Math.cos(rad) * 0.6;
  const perpY = -Math.sin(rad) * 0.6;
  project.walls.push({
    id: 'w-' + Date.now(),
    start: { x: +(cx - perpX).toFixed(2), y: +(cy - perpY).toFixed(2) },
    end:   { x: +(cx + perpX).toFixed(2), y: +(cy + perpY).toFixed(2) },
    material: 'Drywall / Plaster',
    isDoorway: false
  });
  const res = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(project)});
  project = await res.json();
  renderRecommendations();
  drawMap();
}

async function probeAndRecordSampleAt(pt) {
  try {
    const t0 = performance.now();
    await fetch('/api/ping?t=' + Date.now());
    const rtt = performance.now() - t0;
    rttHistory.push(rtt);
    if (rttHistory.length > 6) rttHistory.shift();
    const mean = rttHistory.reduce((a,b)=>a+b,0) / rttHistory.length;
    const jitter = Math.max(0.4, Math.sqrt(rttHistory.map(x => (x-mean)**2).reduce((a,b)=>a+b,0) / rttHistory.length));

    const td0 = performance.now();
    const downRes = await fetch('/api/download?bytes=2500000&t=' + Date.now());
    const buf = await downRes.arrayBuffer();
    const dt = Math.max(0.005, (performance.now() - td0) / 1000.0);
    const downMbps = (buf.byteLength * 8.0) / (dt * 1e6);

    // Synthesize effective RSSI (dBm) from LAN speed + RTT/jitter stability
    const speedNorm = Math.min(1, Math.max(0, downMbps / 550.0));
    const latNorm = Math.max(0, 1.0 - (rtt - 3.0) / 70.0);
    const estRssi = Math.round(-85 + (0.65 * speedNorm + 0.35 * latNorm) * 43);

    const sample = {
      id: 's-' + Date.now(),
      position: {...pt},
      rssiDbm: estRssi,
      isEstimatedRssi: true,
      downloadMbps: +downMbps.toFixed(1),
      uploadMbps: +(downMbps * 0.55).toFixed(1),
      latencyMs: +rtt.toFixed(1),
      jitterMs: +jitter.toFixed(1),
      packetLossPercent: 0.0
    };
    project.samples.push(sample);
    updateTelemetryUI(sample);

    const res = await fetch('/api/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(project)});
    project = await res.json();
    renderRecommendations();
    drawMap();
  } catch (e) {}
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
  document.getElementById('uiSamplesCount').textContent = `${(project.samples || []).length} samples`;
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
      <div style="font-weight:700;color:#c084fc;margin-bottom:4px;font-size:14px;">${rec.title}</div>
      <div style="font-size:12px;color:#cbd5e1;margin-bottom:8px;">${rec.summary}</div>
      <div style="font-size:11px;margin-bottom:8px;">
        <span style="color:#4ade80;font-weight:700;">Good Coverage: ${rec.currentGoodCoveragePercent}% → ${rec.predictedGoodCoveragePercent}%</span><br/>
        <span style="color:#fb923c;font-weight:700;">Dead Zones: ${rec.currentDeadZonePercent}% → ${rec.predictedDeadZonePercent}%</span>
      </div>
      <button class="btn purple" onclick="previewRec(${idx})">Preview Heatmap</button>
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
  return `rgba(${r},${g},${b},0.62)`;
}

function drawMap() {
  if (!project) return;
  const canvas = document.getElementById('floorCanvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const ppm = 52.0;
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

  // Heatmap
  for (let gx = -5.8; gx <= 5.8; gx += 0.35) {
    for (let gy = -3.8; gy <= 3.8; gy += 0.35) {
      let bestRssi = -92;
      activeNodes.forEach(n => {
        const d = Math.max(0.6, Math.hypot(n.position.x - gx, n.position.y - gy));
        let rssi = 21.0 - 46.5 - 31.0 * Math.log10(d) - 5.5;
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

  // Walked Trajectory
  if (project.walkedTrajectory && project.walkedTrajectory.length > 1) {
    ctx.beginPath();
    project.walkedTrajectory.forEach((pt, i) => {
      const sx = cx + pt.x * ppm, sy = cy - pt.y * ppm;
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.setLineDash([]);
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

  // Samples
  (project.samples || []).forEach(s => {
    const sx = cx + s.position.x * ppm, sy = cy - s.position.y * ppm;
    ctx.beginPath();
    ctx.arc(sx, sy, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
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

  // Current User Pose + Compass Radar Cone
  const ux = cx + userPos.x * ppm;
  const uy = cy - userPos.y * ppm;
  const rad = ((userHeadingDeg - 90) * Math.PI) / 180.0;
  ctx.beginPath();
  ctx.moveTo(ux, uy);
  ctx.arc(ux, uy, 28, rad - 0.45, rad + 0.45);
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

document.getElementById('floorCanvas').addEventListener('click', async (e) => {
  if (!project) return;
  const rect = e.target.getBoundingClientRect();
  const scaleX = e.target.width / rect.width;
  const scaleY = e.target.height / rect.height;
  const sx = (e.clientX - rect.left) * scaleX;
  const sy = (e.clientY - rect.top) * scaleY;
  const ppm = 52.0;
  const wx = +((sx - e.target.width / 2) / ppm).toFixed(2);
  const wy = +((e.target.height / 2 - sy) / ppm).toFixed(2);

  if (clickMode === 'router') {
    project.nodes = [{id: 'r-' + Date.now(), name: 'Primary Router', role: 'Primary Router', position: {x:wx, y:wy}, band: '5 GHz', txPowerDbm: 21.0, backhaul: 'Wired Ethernet Backhaul'}];
    clickMode = 'sample';
  } else if (clickMode === 'mesh') {
    project.nodes.push({id: 'm-' + Date.now(), name: 'Mesh Node #' + project.nodes.length, role: 'Mesh Satellite / Extender', position: {x:wx, y:wy}, band: '5 GHz', txPowerDbm: 21.0, backhaul: 'Wireless Mesh Backhaul'});
    clickMode = 'sample';
  } else if (clickMode === 'wall') {
    if (!pendingWallStart) {
      pendingWallStart = {x: wx, y: wy};
      document.getElementById('statusText').textContent = 'Now tap the end point of the wall';
      return;
    } else {
      project.walls.push({id: 'w-' + Date.now(), start: pendingWallStart, end: {x: wx, y: wy}, material: 'Drywall / Plaster', isDoorway: false});
      pendingWallStart = null;
      clickMode = 'sample';
    }
  } else {
    userPos = {x: wx, y: wy};
    await probeAndRecordSampleAt(userPos);
    return;
  }
  previewNodes = null;
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

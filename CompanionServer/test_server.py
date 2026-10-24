import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from CompanionServer.server import (
    CompanionHandler,
    compute_recommendations,
    default_demo_project,
    evaluate_network_at_point,
    predict_rssi,
)


class TestWiFiMapperEngineAndCompanionServer(unittest.TestCase):
    def test_multi_wall_rf_attenuation(self):
        router = {
            "id": "r1",
            "name": "Router",
            "role": "Primary Router",
            "position": {"x": 0.0, "y": 0.0},
            "band": "5 GHz",
            "txPowerDbm": 21.0,
        }
        target = {"x": 5.0, "y": 0.0}
        free_rssi = predict_rssi(router, target, [])
        brick_wall = [
            {
                "id": "w1",
                "start": {"x": 2.5, "y": -3.0},
                "end": {"x": 2.5, "y": 3.0},
                "material": "Brick / Masonry",
                "isDoorway": False,
            }
        ]
        blocked_rssi = predict_rssi(router, target, brick_wall)
        self.assertLess(blocked_rssi, free_rssi - 7.0)

    def test_placement_optimizer_improves_coverage(self):
        demo = default_demo_project()
        recs = compute_recommendations(demo)
        self.assertGreaterEqual(len(recs), 2)
        mesh_rec = recs[0]
        self.assertGreater(
            mesh_rec["predictedGoodCoveragePercent"],
            mesh_rec["currentGoodCoveragePercent"],
        )
        self.assertLess(
            mesh_rec["predictedDeadZonePercent"],
            mesh_rec["currentDeadZonePercent"],
        )

    def test_http_endpoints_ping_download_upload_sync(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CompanionHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            # 1. Ping
            with urllib.request.urlopen(f"{base}/api/ping") as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

            # 2. Download burst
            with urllib.request.urlopen(f"{base}/api/download?bytes=65536") as resp:
                self.assertEqual(resp.status, 200)
                payload = resp.read()
                self.assertEqual(len(payload), 65536)

            # 3. Upload burst
            req = urllib.request.Request(
                f"{base}/api/upload",
                data=b"A" * 32768,
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                up_res = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(up_res["receivedBytes"], 32768)

            # 4. Project & Sync
            with urllib.request.urlopen(f"{base}/api/project") as resp:
                proj = json.loads(resp.read().decode("utf-8"))
                self.assertIn("recommendations", proj)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

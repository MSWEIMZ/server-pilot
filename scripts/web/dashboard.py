#!/usr/bin/env python3
"""Server Pilot Web Dashboard.

Launch a local web dashboard that shows real-time GPU, training, and system status
by polling the remote server via SSH.

Usage:
    python dashboard.py                    # Start on port 8765
    python dashboard.py --port 9000        # Custom port
    python dashboard.py --server gpu-box   # Multi-server
    python dashboard.py --interval 10      # Refresh every 10s
"""

import argparse
import json
import os
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# Import monitor functions
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server_monitor import load_config, resolve_server, _connect, gpu_info, gpu_procs, train_procs, parse_logs, sys_info

# Shared state
cached_data = {"gpus": [], "training": [], "logs": {}, "system": {}, "error": None, "updated": 0}
config = {}
server_name = None

def poll_server():
    time.sleep(2)  # Wait for HTTP server to start
    """Background thread: poll server for data."""
    global cached_data
    while True:
        try:
            srv = resolve_server(config, server_name)
            ssh = _connect(srv.get("host",""), srv.get("port",22),
                          srv.get("username","root"), srv.get("password",""), srv.get("key_file",""))
            try:
                g = gpu_info(ssh)
                t = train_procs(ssh)
                lg = {}
                for p in t:
                    li = parse_logs(ssh, p['pid'])
                    if li: lg[str(p['pid'])] = li
                s = sys_info(ssh)
                cached_data = {"gpus": g, "training": t, "logs": lg, "system": s, "error": None, "updated": time.time()}
            finally:
                ssh.close()
        except Exception as e:
            cached_data["error"] = str(e)
            cached_data["updated"] = time.time()
        time.sleep(10)


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
            with open(html_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        elif path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(cached_data, ensure_ascii=False, default=str).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logs


def main():
    parser = argparse.ArgumentParser(description="Server Pilot Web Dashboard")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--server", "-s", help="Server name for multi-server config")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")
    args = parser.parse_args()

    global config, server_name
    config = load_config()
    server_name = args.server

    srv = resolve_server(config, server_name)
    if not srv.get("host"):
        print("Error: No host configured in server_config.json", file=sys.stderr)
        sys.exit(1)

    print(f"Server Pilot Dashboard starting...")
    print(f"  Target: {srv.get('username','root')}@{srv.get('host','')}:{srv.get('port',22)}")
    print(f"  URL: http://localhost:{args.port}")
    print(f"  Refresh: every {args.interval}s")
    print(f"  Press Ctrl+C to stop\n")

    # Start background polling
    poll_thread = threading.Thread(target=poll_server, daemon=True)
    poll_thread.start()

    # Initial data fetch
    time.sleep(2)

    # Open browser
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    # Start HTTP server
    server = HTTPServer(('0.0.0.0', args.port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()


if __name__ == "__main__":
    main()


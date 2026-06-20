#!/usr/bin/env python3
"""Server Pilot Web Dashboard."""
import argparse, json, os, sys, time, threading, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server_monitor import load_config, resolve_server, _connect, _cmd, gpu_info, train_procs, parse_logs, sys_info

cached = {"gpus":[],"training":[],"logs":{},"system":{},"processes":[],"network":[],"error":None,"updated":0,"server_name":""}
config = {}; server_name = None; poll_interval = 10

def get_procs(ssh):
    raw = _cmd(ssh, "ps aux --sort=-%cpu | head -11", t=5)
    out = []
    if raw:
        for l in raw.strip().split("\n")[1:]:
            p = l.split(None, 10)
            if len(p) >= 11: out.append({"user":p[0],"pid":p[1],"cpu":p[2],"mem":p[3],"cmd":p[10][:100]})
    return out

def get_net(ssh):
    raw = _cmd(ssh, "cat /proc/net/dev 2>/dev/null | grep -v lo | head -5", t=5)
    nets = []
    if raw:
        for l in raw.strip().split("\n"):
            if ":" in l:
                iface, data = l.split(":", 1)
                p = data.split()
                if len(p) >= 9: nets.append({"iface":iface.strip(),"rx":round(int(p[0])/1024),"tx":round(int(p[8])/1024)})
    return nets

def poll():
    global cached
    time.sleep(2)
    while True:
        try:
            srv = resolve_server(config, server_name)
            ssh = _connect(srv.get("host",""),srv.get("port",22),srv.get("username","root"),srv.get("password",""),srv.get("key_file",""))
            try:
                g=gpu_info(ssh); t=train_procs(ssh); lg={}
                for p in t:
                    li=parse_logs(ssh,p["pid"])
                    if li: lg[str(p["pid"])]=li
                s=sys_info(ssh); pr=get_procs(ssh); n=get_net(ssh)
                cached={"gpus":g,"training":t,"logs":lg,"system":s,"processes":pr,"network":n,"error":None,"updated":time.time(),"server_name":srv.get("host","")}
            finally: ssh.close()
        except Exception as e:
            cached["error"]=str(e); cached["updated"]=time.time()
        time.sleep(poll_interval)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p=urlparse(self.path).path
        if p in("/","/index.html"):
            hp=os.path.join(os.path.dirname(os.path.abspath(__file__)),"dashboard.html")
            with open(hp,"r",encoding="utf-8") as f: c=f.read()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
            self.wfile.write(c.encode("utf-8"))
        elif p=="/api/status":
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin","*"); self.end_headers()
            self.wfile.write(json.dumps(cached,ensure_ascii=False,default=str).encode("utf-8"))
        else: self.send_response(404); self.end_headers()
    def log_message(self,*a): pass

def main():
    global config, server_name, poll_interval
    pa=argparse.ArgumentParser(); pa.add_argument("--port",type=int,default=8765)
    pa.add_argument("--server","-s"); pa.add_argument("--no-browser",action="store_true"); pa.add_argument("--interval",type=int,default=10)
    a=pa.parse_args(); config=load_config(); server_name=a.server; poll_interval=a.interval
    srv=resolve_server(config,server_name)
    if not srv.get("host"): print("Error: No host.",file=sys.stderr); sys.exit(1)
    print(f"  Server Pilot Dashboard\n  {srv.get('username','root')}@{srv.get('host','')}:{srv.get('port',22)}\n  http://localhost:{a.port}\n  Ctrl+C to stop")
    threading.Thread(target=poll,daemon=True).start()
    if not a.no_browser: threading.Timer(1.5,lambda:webbrowser.open(f"http://localhost:{a.port}")).start()
    s=HTTPServer(("0.0.0.0",a.port),H)
    try: s.serve_forever()
    except KeyboardInterrupt: print("\nStopped."); s.server_close()

if __name__=="__main__": main()

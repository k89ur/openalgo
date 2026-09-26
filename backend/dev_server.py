#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".pipsgox"
CONTROL_PORT = int(os.getenv("PIPSGOX_CONTROL_PORT", "9000"))
DOMAIN = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev").strip()
CODESPACE = os.getenv("CODESPACE_NAME", "").strip()
START_SCRIPT = ROOT / "scripts" / "run-pipsgox.sh"
STOP_SCRIPT = ROOT / "scripts" / "stop-pipsgox.sh"

def forwarded_url(port):
    if CODESPACE and DOMAIN:
        return f"https://{CODESPACE}-{port}.{DOMAIN}"
    return f"http://127.0.0.1:{port}"

def pid_running(name):
    path = RUN_DIR / f"{name}.pid"
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False

def tail_log(name, lines=100):
    safe = "backend" if name == "backend" else "frontend"
    path = RUN_DIR / f"{safe}.log"
    if not path.exists():
        return "Log file not found."
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-max(1, min(lines, 200)):])
    except OSError as exc:
        return f"Could not read log: {exc}"

def backend_health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=1.5) as response:
            return {"running": True, "health": json.loads(response.read().decode("utf-8"))}
    except Exception:
        return {"running": False}

def fyers_status():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/fyers/status", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"configured": bool(os.getenv("FYERS_CLIENT_ID") and os.getenv("FYERS_SECRET_KEY")), "connected": False}

def current_status():
    return {
        "control": True, "control_port": CONTROL_PORT,
        "backend": pid_running("backend"), "frontend": pid_running("frontend"),
        "backend_url": forwarded_url(8000), "frontend_url": forwarded_url(3001),
        "control_url": forwarded_url(CONTROL_PORT),
        "backend_health": backend_health(), "fyers": fyers_status(),
    }

def launch(script):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["bash", str(script)], cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PIPSGOX Dev Control</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#101114;color:#e6e8eb;font-family:Inter,system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:22px 16px 40px}h1{font-size:20px;margin:0 0 4px}.sub{color:#8d9299;font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card{background:#17191d;border:1px solid #292c32;border-radius:10px;padding:14px}
.label{font-size:10px;color:#7e848c;text-transform:uppercase;letter-spacing:.08em}.value{margin-top:8px;font-size:14px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#5b626b;margin-right:7px}.dot.on{background:#35c98a}.dot.warn{background:#e6a93d}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}button{border:1px solid #343941;background:#1d2025;color:#e9ebee;border-radius:7px;padding:9px 15px;font-weight:600;cursor:pointer}
button:hover{background:#272b31}button.primary{background:#1f6feb;border-color:#2b7de9}button.danger{background:#9e3038;border-color:#b63c45}
.links{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}a{color:#79aefc;text-decoration:none;font-size:12px}.panel{margin-top:14px;background:#17191d;border:1px solid #292c32;border-radius:10px;padding:14px}
.panel h2{font-size:12px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.08em;color:#a8adb4}
pre{margin:0;white-space:pre-wrap;word-break:break-word;background:#0d0f12;border:1px solid #24272c;border-radius:7px;padding:10px;min-height:100px;max-height:280px;overflow:auto;font-size:11px;line-height:1.45;color:#b9bec5}
.small{font-size:11px;color:#7e848c}@media(max-width:650px){.grid{grid-template-columns:repeat(2,1fr)}main{padding:16px 10px 30px}}
</style></head><body><main><h1>PIPSGOX DEV CONTROL PANEL</h1><div class="sub">Independent control service · port 9000</div>
<div class="grid"><div class="card"><div class="label">Control</div><div class="value"><span class="dot on"></span>ONLINE</div></div>
<div class="card"><div class="label">Backend</div><div class="value" id="backend">Checking...</div></div><div class="card"><div class="label">Frontend</div><div class="value" id="frontend">Checking...</div></div>
<div class="card"><div class="label">FYERS</div><div class="value" id="fyers">Checking...</div></div></div>
<div class="actions"><button class="primary" onclick="action('start')">START</button><button class="danger" onclick="action('stop')">STOP</button><button onclick="action('restart')">RESTART</button><button onclick="refresh()">REFRESH</button></div>
<div class="links" id="links"></div><div class="panel"><h2>Backend log</h2><pre id="backendLog">Loading...</pre></div>
<div class="panel"><h2>Frontend log</h2><pre id="frontendLog">Loading...</pre></div>
<script>
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));function state(v){return v?'<span class="dot on"></span>RUNNING':'<span class="dot"></span>STOPPED'}
async function refresh(){const s=await fetch('/api/status').then(r=>r.json());document.getElementById('backend').innerHTML=state(s.backend);document.getElementById('frontend').innerHTML=state(s.frontend);
document.getElementById('fyers').innerHTML=s.fyers?.connected?'<span class="dot on"></span>CONNECTED':(s.fyers?.configured?'<span class="dot warn"></span>LOGIN REQUIRED':'<span class="dot"></span>NOT CONFIGURED');
document.getElementById('links').innerHTML='<a href="'+s.frontend_url+'" target="_blank">OPEN PIPSGOX ↗</a><a href="'+s.backend_url+'/docs" target="_blank">API DOCS ↗</a><span class="small">Control: '+esc(s.control_url)+'</span>';
const logs=await Promise.all(['backend','frontend'].map(n=>fetch('/api/logs/'+n).then(r=>r.text())));document.getElementById('backendLog').textContent=logs[0];document.getElementById('frontendLog').textContent=logs[1]}
async function action(name){const response=await fetch('/api/'+name,{method:'POST'});const data=await response.json();if(!response.ok)alert(data.error||'Action failed');setTimeout(refresh,1000)}refresh();setInterval(refresh,3000);
</script></main></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def _send(self,status,body,content_type="text/html; charset=utf-8"):
        data=body.encode("utf-8");self.send_response(status);self.send_header("Content-Type",content_type);self.send_header("Content-Length",str(len(data)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=="/": self._send(200,HTML); return
        if self.path=="/api/status": self._send(200,json.dumps(current_status()),"application/json"); return
        if self.path.startswith("/api/logs/"): self._send(200,tail_log(self.path.rsplit("/",1)[-1]),"text/plain; charset=utf-8"); return
        self._send(404,"Not found","text/plain; charset=utf-8")
    def do_POST(self):
        if self.path not in {"/api/start","/api/stop","/api/restart"}: self._send(404,json.dumps({"error":"Not found"}),"application/json"); return
        try:
            if self.path=="/api/start": launch(START_SCRIPT)
            elif self.path=="/api/stop": launch(STOP_SCRIPT)
            else: subprocess.Popen(["bash","-lc",f"sleep 1; bash {STOP_SCRIPT!s}; sleep 1; bash {START_SCRIPT!s}"],cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self._send(200,json.dumps({"ok":True}),"application/json")
        except OSError as exc: self._send(500,json.dumps({"error":str(exc)}),"application/json")
    def log_message(self,format,*args): return

if __name__=="__main__":
    RUN_DIR.mkdir(parents=True,exist_ok=True);server=ThreadingHTTPServer(("0.0.0.0",CONTROL_PORT),Handler)
    print(f"PIPSGOX Dev Control listening on :{CONTROL_PORT}",flush=True);server.serve_forever()

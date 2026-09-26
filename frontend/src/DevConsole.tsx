import { useCallback, useEffect, useState } from "react";

type DevStatus = {
  backend: boolean;
  frontend: boolean;
  fyers_configured: boolean;
  fyers_connected: boolean;
  web_url: string;
  api_url: string;
};

function Dot({ on }: { on: boolean }) {
  return <span className={`dev-dot ${on ? "is-on" : "is-off"}`} />;
}

export function DevConsole() {
  const [status, setStatus] = useState<DevStatus | null>(null);
  const [logName, setLogName] = useState<"backend" | "frontend">("backend");
  const [logs, setLogs] = useState("Loading...");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/dev/status", { cache: "no-store" });
      if (!response.ok) throw new Error("Dev API unavailable");
      setStatus(await response.json() as DevStatus);
    } catch {
      setStatus(null);
    }
  }, []);

  const loadLogs = useCallback(async () => {
    try {
      const response = await fetch(`/api/dev/logs/${logName}?lines=100`, { cache: "no-store" });
      if (!response.ok) throw new Error("Log request failed");
      const data = await response.json() as { content: string };
      setLogs(data.content || "No log output.");
    } catch {
      setLogs("Dev API unavailable.");
    }
  }, [logName]);

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 2500);
    return () => window.clearInterval(timer);
  }, [loadStatus]);

  useEffect(() => {
    void loadLogs();
    const timer = window.setInterval(() => void loadLogs(), 3000);
    return () => window.clearInterval(timer);
  }, [loadLogs]);

  const action = async (name: "start" | "stop" | "restart") => {
    setBusy(name);
    setMessage(name === "restart" ? "Restart requested..." : `${name[0].toUpperCase() + name.slice(1)} requested...`);
    try {
      const response = await fetch(`/api/dev/${name}`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Request failed.");
    } finally {
      window.setTimeout(() => {
        setBusy("");
        void loadStatus();
        void loadLogs();
      }, 1200);
    }
  };

  const openTerminal = () => {
    window.location.href = "/";
  };

  return (
    <div className="dev-console">
      <header className="dev-header">
        <div>
          <div className="dev-brand">PIPSGOX</div>
          <div className="dev-title">DEV CONTROL PANEL</div>
        </div>
        <button className="dev-back" onClick={openTerminal}>OPEN TERMINAL</button>
      </header>

      <main className="dev-content">
        <section className="dev-card">
          <div className="dev-card-title">APPLICATION CONTROL</div>
          <div className="dev-actions">
            <button onClick={() => void action("start")} disabled={!!busy}>START</button>
            <button onClick={() => void action("stop")} disabled={!!busy}>STOP</button>
            <button onClick={() => void action("restart")} disabled={!!busy}>RESTART</button>
          </div>
          <div className="dev-message">{busy ? `${busy.toUpperCase()}ING...` : message || "Ready"}</div>
        </section>

        <section className="dev-grid">
          <div className="dev-card">
            <div className="dev-card-title">STATUS</div>
            <div className="dev-status-row"><span>Backend :8000</span><span><Dot on={!!status?.backend} />{status?.backend ? "RUNNING" : "STOPPED"}</span></div>
            <div className="dev-status-row"><span>Frontend :3001</span><span><Dot on={!!status?.frontend} />{status?.frontend ? "RUNNING" : "STOPPED"}</span></div>
            <div className="dev-status-row"><span>FYERS API</span><span><Dot on={!!status?.fyers_connected} />{status?.fyers_connected ? "CONNECTED" : status?.fyers_configured ? "LOGIN REQUIRED" : "NOT CONFIGURED"}</span></div>
          </div>

          <div className="dev-card">
            <div className="dev-card-title">LINKS</div>
            <div className="dev-link-row"><span>Web</span><code>{status?.web_url || "—"}</code></div>
            <div className="dev-link-row"><span>API</span><code>{status?.api_url || "—"}</code></div>
            <div className="dev-note">API credentials remain server-side. This panel never displays your FYERS secret or access token.</div>
          </div>
        </section>

        <section className="dev-card dev-log-card">
          <div className="dev-log-head">
            <div className="dev-card-title">LIVE LOG</div>
            <div className="dev-log-tabs">
              <button className={logName === "backend" ? "active" : ""} onClick={() => setLogName("backend")}>BACKEND</button>
              <button className={logName === "frontend" ? "active" : ""} onClick={() => setLogName("frontend")}>FRONTEND</button>
              <button onClick={() => void loadLogs()}>REFRESH</button>
            </div>
          </div>
          <pre className="dev-log">{logs}</pre>
        </section>
      </main>
    </div>
  );
}

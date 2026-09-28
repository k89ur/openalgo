import { useEffect, useState } from "react";
import { apiFetch } from "./api";
import { StatusCenter, type DiagnosticCheck } from "./StatusCenter";

type Props = {
  accountId: number | null;
  accountName: string;
  broker: string;
  onReady: () => void;
  onLogout: () => void;
};

type CheckResponse = {
  ready: boolean;
  status: string;
  checks: DiagnosticCheck[];
  summary: Record<string, unknown>;
};

export function StartupGate({ accountId, accountName, broker, onReady, onLogout }: Props) {
  const [result, setResult] = useState<CheckResponse | null>(null);
  const [running, setRunning] = useState(true);
  const [error, setError] = useState("");

  const run = async () => {
    if (!accountId) {
      setError("No connected broker account is available. PIPSGOX cannot start.");
      setRunning(false);
      return;
    }
    setRunning(true);
    setError("");
    try {
      const response = await apiFetch("/api/startup/check/" + accountId, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "PIPSGOX startup check could not be completed.");
      setResult(payload as CheckResponse);
      if (payload.ready) onReady();
    } catch (err) {
      setError(err instanceof Error ? err.message : "PIPSGOX startup check failed.");
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => { void run(); }, [accountId]);

  if (running) {
    return (
      <main className="startup-screen">
        <div className="startup-card">
          <div className="auth-brand">PIPSGOX</div>
          <div className="startup-spinner" />
          <h1>CHECKING APPLICATION</h1>
          <p>Validating {broker.toUpperCase()} · {accountName} and testing the PIPSGOX data path.</p>
          <div className="startup-progress">Broker → Symbol Master → Quote → History → Account APIs</div>
        </div>
      </main>
    );
  }

  if (result?.ready) return null;

  const checks = result?.checks || [];
  const failed = checks.filter((item) => item.status === "ERROR");
  const warnings = checks.filter((item) => item.status === "WARNING");

  return (
    <main className="startup-screen">
      <div className="startup-failed-card">
        <div className="auth-brand">PIPSGOX</div>
        <div className="startup-failed-icon">×</div>
        <div className="startup-eyebrow">APPLICATION NOT READY</div>
        <h1>STARTUP CHECK FAILED</h1>
        <p className="startup-lead">
          Broker authentication may have succeeded, but PIPSGOX found a blocking problem before opening the main application.
        </p>

        {error && <div className="startup-error-box"><strong>Startup engine error</strong><span>{error}</span></div>}

        <div className="startup-account">
          <span>ACCOUNT</span><strong>{accountName}</strong><small>{broker.toUpperCase()}</small>
        </div>

        <div className="startup-results">
          {checks.map((check) => (
            <div className={"startup-result " + check.status.toLowerCase()} key={check.id}>
              <span className="startup-result-icon">{check.status === "OK" ? "✓" : check.status === "WARNING" ? "!" : "×"}</span>
              <div><strong>{check.label}</strong><span>{check.message}</span>{check.error_code && <code>{check.error_code}</code>}</div>
            </div>
          ))}
        </div>

        <div className="startup-summary">
          <span>{failed.length} blocking error{failed.length === 1 ? "" : "s"}</span>
          <span>{warnings.length} warning{warnings.length === 1 ? "" : "s"}</span>
        </div>

        <div className="startup-actions">
          <button className="status-primary" onClick={() => void run()}>RETRY FULL CHECK</button>
          <button className="status-secondary" onClick={onLogout}>LOG OUT</button>
        </div>

        <details className="startup-advanced">
          <summary>ADVANCED DIAGNOSTICS</summary>
          <StatusCenter accountId={accountId} embedded />
        </details>
      </div>
    </main>
  );
}

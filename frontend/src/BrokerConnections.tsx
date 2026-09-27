import { useEffect, useState } from "react";
import { apiFetch } from "./api";

type BrokerAccount = {
  id: number;
  broker: string;
  account_name: string;
  client_id: string;
  status: string;
  created_at: string;
  updated_at: string;
};

type Props = {
  onClose: () => void;
};

const BROKERS = [
  { value: "fyers", label: "FYERS" },
  { value: "dhan", label: "Dhan" },
];

export function BrokerConnections({ onClose }: Props) {
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [broker, setBroker] = useState("fyers");
  const [accountName, setAccountName] = useState("");
  const [clientId, setClientId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");

  const loadAccounts = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/broker/accounts");
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not load broker accounts.");
      setAccounts(Array.isArray(payload) ? payload : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load broker accounts.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAccounts();
  }, []);

  const addAccount = async () => {
    setError("");
    setMessage("");

    if (!accountName.trim()) {
      setError("Account name is required.");
      return;
    }
    if (!apiSecret.trim()) {
      setError("API secret is required.");
      return;
    }

    setSaving(true);
    try {
      const response = await apiFetch("/api/broker/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          broker,
          account_name: accountName.trim(),
          client_id: clientId.trim(),
          api_secret: apiSecret,
          api_key: apiKey,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not save broker account.");

      setAccounts((current) => [...current, payload as BrokerAccount]);
      setAccountName("");
      setClientId("");
      setApiKey("");
      setApiSecret("");
      setMessage("Broker account saved securely.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save broker account.");
    } finally {
      setSaving(false);
    }
  };

  const connectAccount = async (account: BrokerAccount) => {
    setError("");
    setMessage("");
    try {
      const response = await apiFetch("/api/broker/accounts/" + account.id + "/connect");
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not start broker login.");
      if (!payload.authorization_url) throw new Error("Broker did not return a login URL.");
      window.location.assign(payload.authorization_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start broker login.");
    }
  };

  const removeAccount = async (id: number) => {
    setError("");
    setMessage("");
    setDeletingId(id);
    try {
      const response = await apiFetch("/api/broker/accounts/" + id, { method: "DELETE" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Could not delete broker account.");
      setAccounts((current) => current.filter((account) => account.id !== id));
      setMessage("Broker account removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete broker account.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="modal-backdrop broker-modal-backdrop" onClick={onClose}>
      <section className="modal broker-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <strong>ACCOUNT / BROKER CONNECTIONS</strong>
            <span className="broker-modal-subtitle">Manage trading accounts securely</span>
          </div>
          <button onClick={onClose}>CLOSE</button>
        </div>

        <div className="broker-panel">
          <div className="broker-section-title">CONNECTED ACCOUNTS</div>

          {loading ? (
            <div className="broker-empty">Loading broker accounts...</div>
          ) : accounts.length ? (
            <div className="broker-account-list">
              {accounts.map((account) => (
                <div className="broker-account-row" key={account.id}>
                  <div className="broker-account-main">
                    <div className="broker-account-name">{account.account_name}</div>
                    <div className="broker-account-meta">
                      <span>{account.broker.toUpperCase()}</span>
                      <span>{account.client_id || "Client ID not set"}</span>
                    </div>
                  </div>
                  <div className="broker-account-status">
                    <span className={"broker-status-dot " + (account.status === "connected" ? "connected" : "")} />
                    <span>{account.status}</span>
                  </div>
                  <button
                    className="broker-connect"
                    onClick={() => void connectAccount(account)}
                  >
                    CONNECT
                  </button>
                  <button
                    className="broker-delete"
                    disabled={deletingId === account.id}
                    onClick={() => void removeAccount(account.id)}
                  >
                    {deletingId === account.id ? "..." : "REMOVE"}
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="broker-empty">No broker accounts added yet.</div>
          )}

          <div className="broker-divider" />

          <div className="broker-section-title">ADD BROKER ACCOUNT</div>
          <div className="broker-form">
            <label>
              <span>Broker</span>
              <select value={broker} onChange={(event) => setBroker(event.target.value)}>
                {BROKERS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>

            <label>
              <span>Account name</span>
              <input
                value={accountName}
                onChange={(event) => setAccountName(event.target.value)}
                placeholder="e.g. Main Trading"
                autoComplete="off"
              />
            </label>

            <label>
              <span>Client ID</span>
              <input
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="Broker client ID"
                autoComplete="off"
              />
            </label>

            <label>
              <span>API Key / App ID <small>(Dhan)</small></span>
              <input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Required for Dhan"
                autoComplete="off"
              />
            </label>

            <label>
              <span>API secret</span>
              <input
                type="password"
                value={apiSecret}
                onChange={(event) => setApiSecret(event.target.value)}
                placeholder="Stored encrypted on the server"
                autoComplete="new-password"
              />
            </label>
          </div>

          {error && <div className="broker-message broker-error">{error}</div>}
          {message && <div className="broker-message broker-success">{message}</div>}

          <div className="broker-security-note">
            API secrets are encrypted in the backend database and are never returned to the browser after saving.
          </div>

          <div className="broker-actions">
            <button className="broker-cancel" onClick={onClose}>Cancel</button>
            <button className="broker-primary" disabled={saving} onClick={() => void addAccount()}>
              {saving ? "Saving..." : "Add account"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

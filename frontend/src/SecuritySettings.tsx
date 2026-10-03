import { useEffect, useState } from "react";
import { apiFetch } from "./api";
import { passkeySupported, registerPasskey } from "./webauthn";

type PasskeyItem = {
  id: number;
  device_name: string | null;
  created_at: string;
  last_used_at: string | null;
};

type SecuritySettingsProps = {
  onClose: () => void;
};

export function SecuritySettings({ onClose }: SecuritySettingsProps) {
  const [passkeys, setPasskeys] = useState<PasskeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadPasskeys = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/api/auth/passkeys", { cache: "no-store" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || "Could not load passkeys."));
      setPasskeys(Array.isArray(payload.passkeys) ? payload.passkeys : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load passkeys.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadPasskeys();
  }, []);

  const addPasskey = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await registerPasskey();
      setMessage("Passkey added successfully. You can now sign in with your device biometric, PIN, or security key.");
      await loadPasskeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add passkey.");
    } finally {
      setBusy(false);
    }
  };

  const removePasskey = async (id: number) => {
    if (!window.confirm("Remove this passkey? You will no longer be able to use it to sign in to PIPSGOX.")) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch("/api/auth/passkeys/" + id, { method: "DELETE" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || "Could not remove passkey."));
      setPasskeys((items) => items.filter((item) => item.id !== id));
      setMessage("Passkey removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove passkey.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section className="modal security-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <strong>SECURITY</strong>
          <button onClick={onClose}>CLOSE</button>
        </div>
        <div className="security-panel">
          <div className="security-heading">
            <div>
              <div className="security-title">Passkeys</div>
              <div className="security-description">
                Use your Android fingerprint, face unlock, device PIN, or a compatible security key to sign in without typing your password.
              </div>
            </div>
            <span className="security-recommended">RECOMMENDED</span>
          </div>

          {!passkeySupported() && (
            <div className="security-warning">
              Passkeys are not available in this browser/origin. Use HTTPS or a supported localhost origin.
            </div>
          )}

          {error && <div className="security-error">{error}</div>}
          {message && <div className="security-success">{message}</div>}

          <button className="security-primary" disabled={busy || !passkeySupported()} onClick={() => void addPasskey()}>
            {busy ? "WORKING..." : "ADD PASSKEY"}
          </button>

          <div className="security-section-label">REGISTERED PASSKEYS</div>
          {loading ? (
            <div className="security-empty">Loading passkeys...</div>
          ) : passkeys.length === 0 ? (
            <div className="security-empty">No passkeys registered yet.</div>
          ) : (
            <div className="security-passkey-list">
              {passkeys.map((passkey) => (
                <div className="security-passkey" key={passkey.id}>
                  <div>
                    <strong>{passkey.device_name || "Passkey"}</strong>
                    <span>Added {new Date(passkey.created_at).toLocaleString()}</span>
                    {passkey.last_used_at && <span>Last used {new Date(passkey.last_used_at).toLocaleString()}</span>}
                  </div>
                  <button disabled={busy} onClick={() => void removePasskey(passkey.id)}>REMOVE</button>
                </div>
              ))}
            </div>
          )}

          <div className="security-note">
            PIPSGOX never receives or stores your fingerprint, face data, or device PIN. Your device/authenticator performs that verification locally.
          </div>
        </div>
      </section>
    </div>
  );
}

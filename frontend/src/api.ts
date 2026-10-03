const API_REQUEST_TIMEOUT_MS = 15000;

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  // Authentication requests should never leave the UI stuck indefinitely if
  // the backend/database connection is unavailable.
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS);

  try {
    return await fetch(input, {
      ...init,
      credentials: "include",
      signal: init.signal ?? controller.signal,
    });
  } finally {
    window.clearTimeout(timeout);
  }
}

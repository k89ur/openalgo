const API_REQUEST_TIMEOUT_MS = 15000;

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  // Authentication requests should never leave the UI stuck indefinitely if
  // the backend/database connection is unavailable.
  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, API_REQUEST_TIMEOUT_MS);

  try {
    return await fetch(input, {
      ...init,
      credentials: "include",
      signal: init.signal ?? controller.signal,
    });
  } catch (error) {
    if (timedOut) {
      throw new Error("Request timed out after 15 seconds. The PIPSGOX backend or database is taking too long to respond.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

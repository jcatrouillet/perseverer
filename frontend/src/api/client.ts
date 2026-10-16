// Thin fetch wrapper: attaches whichever credential is in localStorage (a JWT from
// POST /auth/login, or a pasted per-athlete API key -- see components/AuthGate.tsx) and
// branches on 401 vs 503 rather than treating every failure alike. See
// docs/ARCHITECTURE.md.
import type { ForgotPasswordResponse, LoginRequest, LoginResponse } from "./types";

const JWT_STORAGE_KEY = "perseverer_jwt";
const API_KEY_STORAGE_KEY = "perseverer_api_key";

export class AuthError extends Error {}
export class ServerUnconfiguredError extends Error {}
/** A password reset link that is invalid, expired or already used. */
export class ResetLinkError extends Error {}

/** A non-2xx response. `detail` is the API's own explanation (FastAPI's `{"detail": ...}`)
 * when the body carries one, so a screen can show why, not just that, it failed. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: string,
    readonly detail: string | null,
  ) {
    super(`request failed (${status}): ${body}`);
  }
}

function errorDetail(body: string): string | null {
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  } catch {
    // Not JSON: an HTML error page from a proxy, say.
  }
  return null;
}

// Fired whenever a stored credential is cleared (a 401 on any request, anywhere in the app --
// not just from the login form). AuthGate listens for this to re-show itself even when the
// 401 came from a background query, e.g. a JWT that expired mid-session.
export const AUTH_CLEARED_EVENT = "perseverer:auth-cleared";

// An empty apiBaseUrl means "this page's own origin": the frontend's nginx proxies /api/ to the
// api container, so production needs no API URL setting at all.
function getBaseUrl(): string {
  const config = window.__PERSEVERER_CONFIG__;
  if (!config) {
    throw new Error("window.__PERSEVERER_CONFIG__ is not set -- did /config.js fail to load?");
  }
  return (config.apiBaseUrl ?? "").replace(/\/$/, "");
}

interface StoredCredential {
  header: "Authorization" | "X-API-Key";
  value: string;
}

function getStoredCredential(): StoredCredential | null {
  const jwt = localStorage.getItem(JWT_STORAGE_KEY);
  if (jwt) return { header: "Authorization", value: `Bearer ${jwt}` };
  const apiKey = localStorage.getItem(API_KEY_STORAGE_KEY);
  if (apiKey) return { header: "X-API-Key", value: apiKey };
  return null;
}

export function hasStoredCredential(): boolean {
  return getStoredCredential() !== null;
}

export function storeJwt(token: string): void {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  localStorage.setItem(JWT_STORAGE_KEY, token);
}

export function storeApiKey(key: string): void {
  localStorage.removeItem(JWT_STORAGE_KEY);
  localStorage.setItem(API_KEY_STORAGE_KEY, key);
}

export function clearCredential(): void {
  localStorage.removeItem(JWT_STORAGE_KEY);
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  window.dispatchEvent(new Event(AUTH_CLEARED_EVENT));
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const credential = getStoredCredential();
  const headers = new Headers(options.headers);
  // A FormData body (file uploads, see apiPostForm) must NOT get an explicit Content-Type --
  // the browser sets the correct `multipart/form-data; boundary=...` itself only when it owns
  // that header; setting "application/json" here would break the multipart parse entirely. A
  // raw file body (apiPostFile) brings its own Content-Type.
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (credential) headers.set(credential.header, credential.value);

  const response = await fetch(`${getBaseUrl()}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearCredential();
    throw new AuthError("authentication failed or expired -- please sign in again");
  }
  if (response.status === 503) {
    throw new ServerUnconfiguredError("server is not configured for authentication");
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(response.status, body, errorDetail(body));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export function apiPut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

// For multipart/form-data bodies (file uploads) -- see request()'s own FormData branch above.
export function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: formData });
}

/** A file as the raw request body (the bulk-export upload), streamed by the browser without
 * the multipart wrapping. */
export function apiPostFile<T>(path: string, file: Blob, contentType: string): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: file,
    headers: { "Content-Type": contentType },
  });
}

// Login has no stored credential yet, so it bypasses `request` entirely.
export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (response.status === 401) {
    throw new AuthError("invalid username or password");
  }
  if (response.status === 503) {
    throw new ServerUnconfiguredError("server is not configured for password login");
  }
  if (!response.ok) {
    throw new Error(`login failed (${response.status})`);
  }
  return (await response.json()) as LoginResponse;
}

// The two forgotten-password calls, also made before any credential exists.
export async function requestPasswordReset(identifier: string): Promise<ForgotPasswordResponse> {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier }),
  });
  if (!response.ok) {
    throw new Error(`password reset request failed (${response.status})`);
  }
  return (await response.json()) as ForgotPasswordResponse;
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  const response = await fetch(`${getBaseUrl()}/api/v1/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (response.status === 400) {
    throw new ResetLinkError("this reset link is invalid, expired or already used");
  }
  if (!response.ok) {
    throw new Error(`password reset failed (${response.status})`);
  }
}

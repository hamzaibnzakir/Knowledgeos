export class ApiError extends Error {
  status: number;
  data: Record<string, unknown>;
  constructor(status: number, message: string, data: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

type Json = Record<string, unknown> | unknown[] | null;

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, url: string, body?: Json | FormData): Promise<T> {
  const headers: Record<string, string> = { "X-KOS": "1" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 401 && !url.startsWith("/api/auth/")) onUnauthorized?.();
  const type = res.headers.get("content-type") ?? "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof data === "object" && data && "detail" in data ? String((data as { detail: unknown }).detail) : res.statusText;
    throw new ApiError(res.status, detail, typeof data === "object" ? (data as Record<string, unknown>) : {});
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: Json | FormData) => request<T>("POST", url, body ?? {}),
  put: <T>(url: string, body: Json) => request<T>("PUT", url, body),
  patch: <T>(url: string, body: Json) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const noteHref = (path: string) => `/note/${path.split("/").map(encodeURIComponent).join("/")}`;
export const fileUrl = (path: string) => `/api/files${qs({ path })}`;

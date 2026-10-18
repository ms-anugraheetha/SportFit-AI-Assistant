import type { ChatResponse, Meta, RequestDetail, RequestPage, SessionInfo, StaffSummary } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// The CSRF token lives only in memory (never in localStorage); /api/staff/me returns it again after a reload.
let csrfToken: string | null = null;

async function request<T>(path: string, init: RequestInit = {}, mutating = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body) headers["Content-Type"] = "application/json";
  if (mutating && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string>) }, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error body */
  }
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(res.status, data));
  }
  return data as T;
}

function errorMessage(status: number, data: unknown): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (status === 422) return "Some of the information was not valid. Please check it and try again.";
  if (status === 429) return "Too many requests. Please wait a minute and try again.";
  if (status >= 500) return "The server had a problem. Please try again shortly.";
  return "Something went wrong. Please try again.";
}

// ---- public

export const getMeta = () => request<Meta>("/api/meta");

export const sendChat = (conversationId: string | null, message: string) =>
  request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });

// ---- staff

export async function staffLogin(username: string, password: string): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/staff/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  csrfToken = info.csrf_token;
  return info;
}

export async function staffMe(): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/staff/me");
  csrfToken = info.csrf_token;
  return info;
}

export async function staffLogout(): Promise<void> {
  try {
    await request<void>("/api/staff/logout", { method: "POST" }, true);
  } finally {
    csrfToken = null;
  }
}

export const getSummary = () => request<StaffSummary>("/api/staff/summary");

export interface ListParams {
  q: string;
  status: string;
  order: "asc" | "desc";
  page: number;
  pageSize: number;
}

export function listRequests(p: ListParams) {
  const qs = new URLSearchParams({ sort: "created_at", order: p.order, page: String(p.page), page_size: String(p.pageSize) });
  if (p.q.trim()) qs.set("q", p.q.trim());
  if (p.status) qs.set("status", p.status);
  return request<RequestPage>(`/api/staff/service-requests?${qs}`);
}

export const getRequest = (ref: string) => request<RequestDetail>(`/api/staff/service-requests/${encodeURIComponent(ref)}`);

export const updateStatus = (ref: string, status: string) =>
  request<RequestDetail>(
    `/api/staff/service-requests/${encodeURIComponent(ref)}/status`,
    { method: "PATCH", body: JSON.stringify({ status }) },
    true,
  );

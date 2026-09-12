export interface HistoryRecord {
  task_id: string;
  title: string;
  style: string;
  lyrics: string;
  seed: number;
  cot: string;
  audio_url: string;
  created_at: string;
  owner?: string | null;
}

export interface TaskView {
  task_id: string;
  status: "pending" | "running" | "succeeded" | "failed";
  title?: string;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  queue_position?: number;
  record?: HistoryRecord;
  error?: string;
}

const TOKEN_KEY = "yue2-admin-token";

export function getAdminToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setAdminToken(t: string) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export interface UserCreds {
  username: string;
  key: string;
}

export interface QuotaInfo {
  name: string;
  quota_total: number;
  quota_used: number;
  quota_left: number | null;
  in_flight?: number;
  enabled: boolean;
  created_at: string;
  last_used_at: string;
}

export interface PublicConfig {
  max_title_len: number;
  max_style_len: number;
  max_lyrics_len: number;
  require_api_key: boolean;
  max_queue: number;
}

const USER_KEY = "yue2-user";

export function getUserCreds(): UserCreds | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (d.username && d.key) return { username: d.username, key: d.key };
    return null;
  } catch {
    return null;
  }
}

export function setUserCreds(username: string, key: string) {
  localStorage.setItem(USER_KEY, JSON.stringify({ username, key }));
}

export function clearUserCreds() {
  localStorage.removeItem(USER_KEY);
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  admin = false,
  user = false,
): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> || {}) };
  if (admin) {
    const tok = getAdminToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
  }
  if (user) {
    const creds = getUserCreds();
    if (creds) headers["X-API-Key"] = `${creds.username}:${creds.key}`;
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      if (typeof err.detail === "string") detail = err.detail;
      else if (Array.isArray(err.detail)) detail = err.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function isSafeAudioUrl(url: string): boolean {
  if (!url) return false;
  if (/^\/audio\/[0-9a-f]{8}\.flac$/.test(url)) return true;
  try {
    const u = new URL(url, window.location.origin);
    return u.origin === window.location.origin && /^\/audio\/[0-9a-f]{8}\.flac$/.test(u.pathname);
  } catch {
    return false;
  }
}

export function formatBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

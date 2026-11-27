import type { RunView } from "./types";

async function detail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
  } catch {
    /* не JSON */
  }
  return `Ошибка сервера: ${r.status}`;
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await detail(r));
  return r.json();
}

export const fetchDemo = () => getJson<RunView>("/api/demo");
export const fetchRun = (id: string) => getJson<RunView>(`/api/runs/${encodeURIComponent(id)}`);

/** 401 от прокси: загрузка закрыта паролем жюри, а логин и пароль не подошли или не введены. */
export class AuthError extends Error {}

export interface Credentials {
  user: string;
  password: string;
}

const basic = (c: Credentials) => "Basic " + btoa(String.fromCharCode(...new TextEncoder().encode(`${c.user}:${c.password}`)));

export async function startRun(file: File, creds?: Credentials | null): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const headers: Record<string, string> = {};
  if (creds && (creds.user || creds.password)) headers.Authorization = basic(creds);
  const r = await fetch("/api/runs", { method: "POST", body: fd, headers });
  if (r.status === 401)
    throw new AuthError(creds?.password ? "Неверный логин или пароль." : "Загрузка доступна жюри: введите логин и пароль.");
  if (r.status === 413) throw new Error("Файл больше 10 МБ.");
  if (!r.ok) throw new Error(await detail(r));
  return (await r.json()).id;
}

export const reportUrl = (id: string) =>
  id === "demo" ? "/api/demo/report.md" : `/api/runs/${encodeURIComponent(id)}/report.md`;

export interface Health {
  title: string;
  pipeline: string;
  busy: boolean;
  uploadAuth: boolean; // прокси закрывает загрузку паролем (заголовок X-Upload-Auth)
}

export async function fetchHealth(): Promise<Health> {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error(await detail(r));
  const j = await r.json();
  return { ...j, uploadAuth: (r.headers.get("X-Upload-Auth") ?? "").toLowerCase() === "basic" };
}

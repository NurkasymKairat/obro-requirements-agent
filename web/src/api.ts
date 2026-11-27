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

export async function startRun(file: File): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/runs", { method: "POST", body: fd });
  if (!r.ok) throw new Error(await detail(r));
  return (await r.json()).id;
}

export const reportUrl = (id: string) =>
  id === "demo" ? "/api/demo/report.md" : `/api/runs/${encodeURIComponent(id)}/report.md`;

export const fetchHealth = () => getJson<{ title: string; pipeline: string; busy: boolean }>("/api/health");

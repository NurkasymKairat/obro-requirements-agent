import { useEffect, useState } from "react";
import { fetchDemo, fetchHealth, fetchRun } from "./api";
import { ResultScreen } from "./components/ResultScreen";
import { UploadScreen } from "./components/UploadScreen";
import { RunProgress } from "./components/RunProgress";
import type { Loaded, RunView } from "./types";

// Маршруты: #/ — загрузка, #/demo — пример, #/runs/<id> — прогон (прогресс, затем результат).
type Route = { name: "home" } | { name: "demo" } | { name: "run"; id: string };

function parse(hash: string): Route {
  const h = hash.replace(/^#\/?/, "");
  if (h === "demo") return { name: "demo" };
  const m = /^runs\/([\w-]+)$/.exec(h);
  if (m) return { name: "run", id: m[1] };
  return { name: "home" };
}

const DEFAULT_TITLE = "Проверка требований";

const go = (path: string) => {
  window.location.hash = path;
};

const isLoaded = (r: RunView | null): r is Loaded => !!r && r.status === "done" && !!r.result && !!r.view && !!r.document;

export default function App() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState(DEFAULT_TITLE); // WEB_TITLE на сервере
  const [uploadAuth, setUploadAuth] = useState(false);

  useEffect(() => {
    fetchHealth().then(
      (h) => {
        const t = typeof h.title === "string" && h.title.trim() ? h.title.trim() : DEFAULT_TITLE;
        setTitle(t);
        document.title = t;
        setUploadAuth(h.uploadAuth);
      },
      () => {
        document.title = DEFAULT_TITLE;
      },
    );
  }, []);

  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  useEffect(() => {
    setError(null);
    setRun(null);
    if (route.name === "demo") {
      fetchDemo().then(setRun, (e) => setError(e.message));
      return;
    }
    if (route.name !== "run") return;
    let stop = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const r = await fetchRun(route.id);
        if (stop) return;
        setRun(r);
        if (r.status === "queued" || r.status === "running") timer = window.setTimeout(poll, 1000);
      } catch (e) {
        if (!stop) setError((e as Error).message);
      }
    };
    poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [route]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [route]);

  if (route.name === "home") return <UploadScreen title={title} authRequired={uploadAuth} onStarted={(id) => go(`/runs/${id}`)} onDemo={() => go("/demo")} />;

  if (error)
    return (
      <div className="center-msg">
        <p className="error">{error}</p>
        <button className="btn" onClick={() => go("/")}>
          На главную
        </button>
      </div>
    );

  if (isLoaded(run)) return <ResultScreen run={run} title={title} onNew={() => go("/")} />;
  if (run && route.name === "run") return <RunProgress run={run} title={title} onNew={() => go("/")} />;
  return <div className="center-msg">Загрузка…</div>;
}

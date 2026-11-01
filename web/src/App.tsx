import { useEffect, useState } from "react";
import { BookOpen } from "lucide-react";
import { getJson, type Meta } from "./api";
import { currentLocation, docHref, parseRoute, type Route } from "./router";
import { Link } from "./components/Link";
import { NavTree } from "./components/NavTree";
import { GraphView } from "./components/GraphView";
import { ImpactView } from "./components/ImpactView";
import { PageView } from "./components/PageView";
import { SearchPalette } from "./components/SearchPalette";
import { SearchResults } from "./components/SearchResults";
import { StatusBanner } from "./components/StatusBanner";
import { Empty } from "./components/States";

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(...currentLocation()));
  useEffect(() => {
    const onPop = () => setRoute(parseRoute(...currentLocation()));
    window.addEventListener("popstate", onPop);
    window.addEventListener("hashchange", onPop);      // static export routes live in the hash
    return () => { window.removeEventListener("popstate", onPop); window.removeEventListener("hashchange", onPop); };
  }, []);
  return route;
}

export function App() {
  const route = useRoute();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [project, setProject] = useState<string>("");

  useEffect(() => {
    getJson<{ project: { slug: string } }>("/status").then(
      (a) => { setMeta(a.meta); setProject(a.data?.project?.slug ?? ""); },
      () => setMeta(null),
    );
  }, [route]);

  const current = route.name === "doc" ? route.pageId : route.name === "overview" ? "overview:system" : null;
  return (
    <div className="app">
      <header className="topbar">
        <Link href={docHref("overview:system")} className="brand"><BookOpen size={18} aria-hidden /> {project || "ai-discovery"}</Link>
        <SearchPalette />
      </header>
      <aside className="sidebar"><NavTree current={current} /></aside>
      <main className="content">
        <StatusBanner meta={meta} />
        {route.name === "overview" && <PageView pageId="overview:system" />}
        {route.name === "doc" && <PageView pageId={route.pageId} />}
        {route.name === "search" && <SearchResults q={route.q} />}
        {route.name === "graph" && <GraphView focus={route.focus} depth={route.depth} />}
        {route.name === "impact" && <ImpactView target={route.target} />}
        {route.name === "unknown" && <Empty>No such view: <code>{route.path}</code></Empty>}
      </main>
    </div>
  );
}

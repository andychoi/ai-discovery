import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useAnswer } from "../useAnswer";
import { docHref } from "../router";
import { Link } from "./Link";
import { Empty, ErrorState, Loading, Showing } from "./States";

interface TreeNode { page_id: string; title: string; children: TreeNode[] }
interface MenuNode { label: string; screens: { page_id: string; label: string }[]; children: MenuNode[] }

const SECTION_LIMIT = 200;
const SECTIONS: [string, string][] = [
  ["domain", "Domains"], ["entity", "Entities"], ["table", "Tables"], ["endpoint", "Endpoints"],
  ["interface", "Interfaces"], ["flow", "Flows"], ["batch_job", "Batch jobs"], ["stored_proc", "Stored procedures"],
];

function Section({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="nav-section">
      <button className="nav-heading" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />} {title}
      </button>
      {open && children}
    </section>
  );
}

function CodeTree({ nodes, current, depth = 0 }: { nodes: TreeNode[]; current: string | null; depth?: number }) {
  return (
    <ul className="nav-list">
      {nodes.map((n) => (
        <CodeNode key={n.page_id} node={n} current={current} depth={depth} />
      ))}
    </ul>
  );
}

function CodeNode({ node, current, depth }: { node: TreeNode; current: string | null; depth: number }) {
  const [open, setOpen] = useState(depth < 1 || (current ?? "").includes(node.title));
  const label = node.page_id.startsWith("file:") ? node.title.split("/").pop() : node.title.split("/").pop() || node.title;
  return (
    <li>
      {node.children.length > 0 && (
        <button className="twisty" onClick={() => setOpen(!open)} aria-label={open ? "collapse" : "expand"}>
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
      )}
      <Link href={docHref(node.page_id)} className={node.page_id === current ? "active" : undefined}>
        {label}
      </Link>
      {open && node.children.length > 0 && <CodeTree nodes={node.children} current={current} depth={depth + 1} />}
    </li>
  );
}

function MenuTree({ node, current }: { node: MenuNode; current: string | null }) {
  return (
    <ul className="nav-list">
      {node.children.map((c) => (
        <li key={c.label}>
          <span className="menu-label">{c.label}</span>
          <MenuTree node={c} current={current} />
        </li>
      ))}
      {node.screens.map((s) => (
        <li key={s.page_id}>
          <Link href={docHref(s.page_id)} className={s.page_id === current ? "active" : undefined}>{s.label}</Link>
        </li>
      ))}
    </ul>
  );
}

export function NavTree({ current }: { current: string | null }) {
  const tree = useAnswer<Record<string, TreeNode[]>>("/tree");
  const menu = useAnswer<MenuNode>("/screens");
  if (tree.state === "loading") return <Loading what="Loading navigation" />;
  if (tree.state === "error") return <ErrorState error={tree.error} />;
  if (tree.answer.status !== "ok") return <Empty>Nothing indexed yet.</Empty>;
  const data = tree.answer.data;
  return (
    <nav aria-label="Wiki navigation">
      <Link href="/" className={current === "overview:system" ? "active nav-top" : "nav-top"}>Overview</Link>
      <Section title="Screens" defaultOpen>
        {menu.state === "done" && menu.answer.status === "ok" && menu.answer.totals.screens ? (
          <MenuTree node={menu.answer.data} current={current} />
        ) : (
          <Empty>No screens (no supported menu format matched).</Empty>
        )}
      </Section>
      {SECTIONS.filter(([t]) => data[t]?.length).map(([type, title]) => (
        <Section key={type} title={`${title} (${data[type].length})`}>
          <ul className="nav-list">
            {data[type].slice(0, SECTION_LIMIT).map((n) => (
              <li key={n.page_id}>
                <Link href={docHref(n.page_id)} className={n.page_id === current ? "active" : undefined}>{n.title}</Link>
              </li>
            ))}
          </ul>
          {data[type].length > SECTION_LIMIT && <Showing shown={SECTION_LIMIT} total={data[type].length} />}
        </Section>
      ))}
      <Section title="Code" defaultOpen={Boolean(current?.startsWith("file:") || current?.startsWith("module:"))}>
        {data.module?.length ? <CodeTree nodes={data.module} current={current} /> : <Empty>No modules.</Empty>}
      </Section>
    </nav>
  );
}

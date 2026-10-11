import type { MouseEvent, ReactNode } from "react";
import { navigate } from "../router";

/** In-app link: History API navigation, normal behaviour for modified clicks. */
export function Link({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(href);
  };
  return (
    <a href={href} onClick={onClick} className={className}>
      {children}
    </a>
  );
}

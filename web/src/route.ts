import { useEffect, useState } from "react";

/** Minimal hash routing ("#/more/battery"): the browser's back button and reloading keep the current page. */
const EVENT = "openampere:route";

function current(): string[] {
  return window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

export function navigate(path: string): void {
  if (`#/${path}` === window.location.hash) return;
  window.history.pushState({ openampere: true }, "", `#/${path}`);
  window.dispatchEvent(new Event(EVENT));
}

/** Back to the parent page: uses the browser history if we came from inside the app, otherwise replaces. */
export function goBack(parent: string): void {
  if (window.history.state?.openampere) {
    window.history.back();
  } else {
    window.history.replaceState(null, "", `#/${parent}`);
    window.dispatchEvent(new Event(EVENT));
  }
}

export function useRoute(): string[] {
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const update = () => setRoute(current());
    window.addEventListener("popstate", update);
    window.addEventListener("hashchange", update);
    window.addEventListener(EVENT, update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("hashchange", update);
      window.removeEventListener(EVENT, update);
    };
  }, []);
  return route;
}

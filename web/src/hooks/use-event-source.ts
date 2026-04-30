import { useEffect, useRef } from "react";

type Handlers = Record<string, (data: unknown) => void>;

/**
 * Subscribes to an SSE stream while the component is mounted. Pass a stable
 * `handlers` object (memoize it with useMemo if it depends on state) so we
 * don't tear down and re-open the stream on every render.
 */
export function useEventSource(
  url: string | null,
  handlers: Handlers,
  options: { enabled?: boolean } = {},
): void {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const enabled = options.enabled ?? true;

  useEffect(() => {
    if (!enabled || !url) return;

    const es = new EventSource(url);
    const wrappedListeners: Array<[string, (e: MessageEvent) => void]> = [];

    for (const name of Object.keys(handlersRef.current)) {
      const fn = (e: MessageEvent) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(e.data);
        } catch {
          parsed = e.data;
        }
        handlersRef.current[name]?.(parsed);
      };
      es.addEventListener(name, fn);
      wrappedListeners.push([name, fn]);
    }

    es.onerror = () => {
      // Auto-close on error; the page can reopen via state changes.
      es.close();
    };

    return () => {
      for (const [name, fn] of wrappedListeners) {
        es.removeEventListener(name, fn);
      }
      es.close();
    };
  }, [url, enabled]);
}

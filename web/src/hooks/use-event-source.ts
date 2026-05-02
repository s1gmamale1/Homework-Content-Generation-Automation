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
        // EventSource fires a NATIVE "error" event whenever the connection
        // closes — including the normal case where the server has finished
        // streaming and ends the response (e.g., a TOC stream sends
        // toc_ready and returns). The native error has no data payload;
        // server-sent error SSE events always include a JSON body.
        // Distinguish the two so we don't surface "Stream failed." every
        // time a stream completes.
        if (name === "error" && (e.data === undefined || e.data === "")) {
          return;
        }
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
      // Connection closed (normal or abnormal). Close our side; the page
      // can reopen on state change. We don't escalate to the user-defined
      // error handler — "connection closed" isn't itself a failure when
      // the server has already streamed the final event.
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

import { useRouter } from 'vue-router';

/**
 * A click handler for a real link to a page of this app. A plain left click
 * goes through the router, as the tab bar does, rather than loading the app
 * again. A click with a modifier or another button keeps its own meaning (a
 * new tab, a new window), which is why it stays a link with an `href`.
 */
export function useInAppLink(): (event: MouseEvent, href: string) => void {
  const router = useRouter();
  return (event, href) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    void router.push(href);
  };
}

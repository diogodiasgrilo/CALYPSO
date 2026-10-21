/** Fade a scroll container's edge ONLY while it actually overflows.
 *
 * WHY A HOOK AND NOT PURE CSS. `.table-scroll` applies a right-edge mask so a
 * cut-off column reads as "scrolls" rather than "broken". But CSS cannot ask
 * whether an element overflows, so the mask applied at EVERY width — and
 * measured on /comparison/ic_0dte the three tables do not overflow at all on
 * desktop (scrollWidth 1374 == clientWidth 1374) or tablet (696 == 696). The
 * fade was therefore dimming the last column of a table that fits, which is
 * strictly worse than no fade: it hides data to signal a scroll that does not
 * exist.
 *
 * One observer for the whole app rather than a change at each of the nine
 * table sites — the containers are found by class, so a new table gets the
 * behaviour by using the class, with nothing to remember.
 *
 * The CSS keys off `[data-overflowing="true"]`, so the default (attribute
 * absent) is NO mask: if this hook never runs, data is shown unfaded. Failing
 * toward "readable" is the right direction for a financial table.
 */
import { useEffect } from "react";

const SELECTOR = ".table-scroll, .nav-scroll";

export function useOverflowFade(): void {
  useEffect(() => {
    const sync = () => {
      document.querySelectorAll<HTMLElement>(SELECTOR).forEach((el) => {
        const over = el.scrollWidth > el.clientWidth + 2;
        if (over) el.dataset.overflowing = "true";
        else delete el.dataset.overflowing;
      });
    };

    sync();

    // Re-measure on resize, on content changes, and on orientation change.
    const ro = new ResizeObserver(sync);
    document.querySelectorAll<HTMLElement>(SELECTOR).forEach((el) => ro.observe(el));
    const mo = new MutationObserver(() => {
      sync();
      document.querySelectorAll<HTMLElement>(SELECTOR).forEach((el) => ro.observe(el));
    });
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", sync);

    return () => {
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", sync);
    };
  }, []);
}

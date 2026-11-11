import { useEffect } from 'react';
import { trackToday } from '../utils/todayEvents.js';

const MIN_VISIBLE_MS = 300;
const MAX_DWELL_MS = 6 * 3600 * 1000;

// Today card exposure: `card_shown` once per mount after 300 ms with >=50% of the
// card on screen in a visible tab; `card_dwell` (ms) for each visible interval.
export default function useCardExposure(ref, slateId, paper) {
  const itemKey = paper.stable_feed_key || paper.item_key;
  const itemId = paper.item_id;
  useEffect(() => {
    const node = ref.current;
    if (!slateId || !node || typeof IntersectionObserver === 'undefined') return undefined;
    const send = (kind, ms = null) => trackToday(slateId, { item_key: itemKey, item_id: itemId, kind, ms });
    let intersecting = false;
    let since = null;
    let timer = null;
    let shown = false;
    const end = () => {
      if (since === null) return;
      clearTimeout(timer);
      const ms = Math.min(Math.round(performance.now() - since), MAX_DWELL_MS);
      since = null;
      if (ms < MIN_VISIBLE_MS) return;
      if (!shown) send('card_shown');
      shown = true;
      send('card_dwell', ms);
    };
    const sync = () => {
      if (!(intersecting && document.visibilityState === 'visible')) return end();
      if (since !== null) return undefined;
      since = performance.now();
      timer = setTimeout(() => {
        if (!shown) send('card_shown');
        shown = true;
      }, MIN_VISIBLE_MS);
      return undefined;
    };
    const observer = new IntersectionObserver(([entry]) => {
      intersecting = entry.isIntersecting;
      sync();
    }, { threshold: 0.5 });
    observer.observe(node);
    document.addEventListener('visibilitychange', sync);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', sync);
      end();
    };
  }, [ref, slateId, itemKey, itemId]);
}

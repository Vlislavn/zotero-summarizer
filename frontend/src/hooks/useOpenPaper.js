import { useCallback, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

// The paper open in the Library detail lives in the URL as `?open=KEY`, so a reload, a link
// and the full-review page's Back all land on the same detail.
//   open(key)  — the first open adds a history entry (system Back closes the detail); moving
//                between papers replaces it, so Back is one step however many papers you read.
//   close()    — an entry this page added is left by going back; a cold entry (a link, a reload
//                that lost the in-page flag, a return from the full review) has no earlier entry
//                of ours, so `open` is just dropped in place and the page is never left.
// `navKeys` = the keys of the displayed, undecided list, in order: ‹ › and «3 of 2012».
export default function useOpenPaper(navKeys = []) {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const key = new URLSearchParams(search).get('open') || null;
  const added = useRef(false);  // the current `open` entry was added by this page
  useEffect(() => { if (!key) added.current = false; }, [key]);

  const open = useCallback((next) => {
    const params = new URLSearchParams(search);
    params.set('open', next);
    if (!key) added.current = true;
    navigate({ pathname, search: `?${params}` }, { replace: Boolean(key) });
  }, [navigate, pathname, search, key]);

  const close = useCallback(() => {
    if (added.current) { added.current = false; navigate(-1); return; }
    const params = new URLSearchParams(search);
    params.delete('open');
    const rest = params.toString();
    navigate({ pathname, search: rest ? `?${rest}` : '' }, { replace: true });
  }, [navigate, pathname, search]);

  const index = key ? navKeys.indexOf(key) : -1;
  const step = (delta) => navKeys[index + delta] ?? null;
  return {
    key, open, close,
    position: index >= 0 ? index + 1 : null,
    total: navKeys.length,
    prev: index >= 0 ? step(-1) : null,
    next: index >= 0 ? step(1) : null,
  };
}

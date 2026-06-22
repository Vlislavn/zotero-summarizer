// University access — the review fleet's browser-based full-text fetch.
//
// For non-arXiv / paywalled papers a headless download can't pass Cloudflare/SSO,
// so the fleet drives a real browser using a profile the user logs into ONCE here.
// The CONFIG fields (enabled / login URL / EZproxy / cookie-browser) are folded
// into the one Settings form (configForm.js maps `university_access`), so the
// single sticky Save commits them — this panel is a CONTROLLED component that keeps
// ONLY the readiness status and the one-time login action.

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { fetchUniversityAccessStatus, runUniversityLogin } from '../../api/libraryApi.js';
import Button from '../ui/Button.jsx';

export default function UniversityAccessPanel({ form, onUpdate }) {
  const statusQuery = useQuery({
    queryKey: ['university-access-status'],
    queryFn: fetchUniversityAccessStatus,
  });
  const [loginNote, setLoginNote] = useState('');

  const loginMutation = useMutation({
    mutationFn: runUniversityLogin,
    onSuccess: (resp) => {
      setLoginNote(
        resp?.started
          ? 'Browser opened — log in, then close the window.'
          : (resp?.reason || 'Could not start login.'),
      );
      // Poll status a few times so "logged in" flips once the session is saved.
      let n = 0;
      const t = setInterval(() => {
        statusQuery.refetch();
        if (++n >= 6) clearInterval(t);
      }, 5000);
    },
  });

  const status = statusQuery.data || {};
  const browserMissing = status.browser_available === false;
  const loggedIn = Boolean(status.logged_in);
  const canLogin = !browserMissing && Boolean(form.ua_login_url);

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500">University access</h3>
        <span
          className={`text-[11px] px-2 py-0.5 rounded-full border ${
            loggedIn
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
              : 'bg-slate-100 text-slate-600 border-slate-200'
          }`}
        >
          {loggedIn ? 'Logged in' : 'Not logged in'}
        </span>
      </div>
      <p className="text-xs text-slate-500">
        For non-arXiv / paywalled papers, the review fleet drives a real browser with your
        institutional session to fetch the PDF. Edit the fields, <strong>Save changes</strong>,
        then log in once; the session persists.
      </p>

      {browserMissing && (
        <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
          Browser support not installed — run{' '}
          <span className="font-mono">uv pip install -e '.[browser]' && patchright install chromium</span>.
        </p>
      )}

      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={Boolean(form.ua_enabled)}
          onChange={(e) => onUpdate('ua_enabled', e.target.checked)}
        />
        Enable browser PDF fetch for the review fleet
      </label>

      <label className="block text-xs text-slate-600">
        Reuse an existing browser login (skip the in-app sign-in)
        <select
          value={form.ua_cookie_browser}
          onChange={(e) => onUpdate('ua_cookie_browser', e.target.value)}
          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm bg-white"
        >
          <option value="">Don't reuse — use the in-app login</option>
          <option value="chrome">Chrome</option>
          <option value="firefox">Firefox</option>
          <option value="edge">Edge</option>
          <option value="brave">Brave</option>
          <option value="safari">Safari (blocked on macOS 15+/26)</option>
        </select>
        <span className="block text-[11px] text-slate-400 mt-1">
          Reads that browser's session cookies so a paper you can already open there fetches without a second login.
          Safari is unreadable on recent macOS (Apple's hardened container) — use Chrome/Firefox. Falls back to the in-app login.
        </span>
      </label>

      <label className="block text-xs text-slate-600">
        Library login URL
        <input
          type="text"
          value={form.ua_login_url}
          onChange={(e) => onUpdate('ua_login_url', e.target.value)}
          placeholder="https://your-library.edu/login"
          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm"
        />
      </label>

      <label className="block text-xs text-slate-600">
        EZproxy prefix <span className="text-slate-400">(optional — blank for SSO/OpenAthens)</span>
        <input
          type="text"
          value={form.ua_ezproxy_prefix}
          onChange={(e) => onUpdate('ua_ezproxy_prefix', e.target.value)}
          placeholder="https://login.ezproxy.myuni.edu/login?url="
          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-mono"
        />
      </label>

      <div className="flex items-center gap-3 flex-wrap pt-1">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => loginMutation.mutate()}
          disabled={!canLogin || loginMutation.isPending}
          title={canLogin ? 'Open a browser window to log into your library' : 'Set a login URL (and install the browser extra), Save, then log in'}
        >
          {loginMutation.isPending ? 'Opening…' : 'Log in to library'}
        </Button>
        {loginNote && <span className="text-xs text-slate-500">{loginNote}</span>}
      </div>
    </div>
  );
}

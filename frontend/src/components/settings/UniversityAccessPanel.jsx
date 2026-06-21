// University access — a self-contained panel (outside the main config form, like
// AdminSection) for the review fleet's browser-based full-text fetch.
//
// For non-arXiv / paywalled papers a headless download can't pass Cloudflare/SSO, so
// the fleet drives a real browser using a profile the user logs into ONCE here. This
// panel: (1) edits the university_access config (enabled / login URL / optional
// EZproxy prefix) via the shared runtime-config query — university_access isn't
// mapped by configForm.js, so it round-trips untouched and never collides with the
// main form's dirty-check; (2) reports readiness; (3) runs the one-time login.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchConfig, updateConfig } from '../../api/settingsApi.js';
import { fetchUniversityAccessStatus, runUniversityLogin } from '../../api/libraryApi.js';
import { humanizeError } from '../../utils/humanizeError.js';

export default function UniversityAccessPanel() {
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ['runtime-config'], queryFn: fetchConfig });
  const statusQuery = useQuery({
    queryKey: ['university-access-status'],
    queryFn: fetchUniversityAccessStatus,
  });

  const ua = configQuery.data?.university_access || {};
  const [form, setForm] = useState(null);
  const [loginNote, setLoginNote] = useState('');

  // Seed the local edit state once from the config (don't clobber edits).
  useEffect(() => {
    if (form === null && configQuery.data) {
      setForm({
        enabled: Boolean(ua.enabled),
        login_url: ua.login_url || '',
        ezproxy_prefix: ua.ezproxy_prefix || '',
      });
    }
  }, [configQuery.data, form, ua.enabled, ua.login_url, ua.ezproxy_prefix]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateConfig({
        ...configQuery.data,
        university_access: { ...ua, ...form },
      }),
    onSuccess: (resp) => {
      if (resp?.config) queryClient.setQueryData(['runtime-config'], resp.config);
      queryClient.invalidateQueries({ queryKey: ['university-access-status'] });
    },
  });

  const loginMutation = useMutation({
    mutationFn: runUniversityLogin,
    onSuccess: (resp) => {
      setLoginNote(resp?.started ? 'Browser opened — log in, then close the window.' : (resp?.reason || 'Could not start login.'));
      // Poll status a few times so "logged in" flips once the session is saved.
      let n = 0;
      const t = setInterval(() => {
        statusQuery.refetch();
        if (++n >= 6) clearInterval(t);
      }, 5000);
    },
  });

  if (configQuery.isLoading || form === null) {
    return (
      <div className="glass rounded-2xl border border-slate-200 p-4 text-sm text-slate-500">
        Loading university access…
      </div>
    );
  }

  const status = statusQuery.data || {};
  const browserMissing = status.browser_available === false;
  const loggedIn = Boolean(status.logged_in);
  const canLogin = !browserMissing && Boolean(form.login_url);

  return (
    <div className="glass rounded-2xl border border-slate-200 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h3 className="text-base font-bold text-slate-900">University access</h3>
        <span
          className={`text-[11px] px-2 py-0.5 rounded-full border ${
            loggedIn
              ? 'bg-emerald-100 text-emerald-800 border-emerald-300'
              : 'bg-slate-100 text-slate-600 border-slate-300'
          }`}
        >
          {loggedIn ? 'Logged in' : 'Not logged in'}
        </span>
      </div>
      <p className="text-xs text-slate-500">
        For non-arXiv / paywalled papers, the review fleet drives a real browser with your
        institutional session to fetch the PDF. Log in once; the session persists.
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
          checked={form.enabled}
          onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
        />
        Enable browser PDF fetch for the review fleet
      </label>

      <label className="block text-xs text-slate-600">
        Library login URL
        <input
          type="text"
          value={form.login_url}
          onChange={(e) => setForm((f) => ({ ...f, login_url: e.target.value }))}
          placeholder="https://your-library.edu/login"
          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm"
        />
      </label>

      <label className="block text-xs text-slate-600">
        EZproxy prefix <span className="text-slate-400">(optional — blank for SSO/OpenAthens)</span>
        <input
          type="text"
          value={form.ezproxy_prefix}
          onChange={(e) => setForm((f) => ({ ...f, ezproxy_prefix: e.target.value }))}
          placeholder="https://login.ezproxy.myuni.edu/login?url="
          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-mono"
        />
      </label>

      <div className="flex items-center gap-3 flex-wrap pt-1">
        <button
          type="button"
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-sm font-semibold hover:bg-slate-700 disabled:bg-slate-300"
        >
          {saveMutation.isPending ? 'Saving…' : 'Save access settings'}
        </button>
        <button
          type="button"
          onClick={() => loginMutation.mutate()}
          disabled={!canLogin || loginMutation.isPending}
          className="px-3 py-1.5 rounded-lg border border-indigo-300 text-indigo-700 text-sm font-semibold hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
          title={canLogin ? 'Open a browser window to log into your library' : 'Set a login URL (and install the browser extra) first'}
        >
          Log in to library
        </button>
        {loginNote && <span className="text-xs text-slate-500">{loginNote}</span>}
        {saveMutation.error && (
          <span className="text-xs text-rose-700">Save failed: {humanizeError(saveMutation.error)}</span>
        )}
      </div>
    </div>
  );
}

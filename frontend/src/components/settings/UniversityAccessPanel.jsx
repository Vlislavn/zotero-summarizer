// Library access — the review fleet's browser-based full-text fetch.
//
// For non-arXiv / paywalled papers a headless download can't pass Cloudflare/SSO,
// so the fleet drives a real browser using a profile the user logs into ONCE here.
// ConfigTask owns the revision-bound library-access draft and scoped save;
// this controlled panel keeps only status and the separate one-time login.

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { fetchUniversityAccessStatus, runUniversityLogin } from '../../api/libraryApi.js';
import { CheckboxField, Field } from '../form/Fields.jsx';
import Button from '../ui/Button.jsx';

const COOKIE_BROWSERS = [
  { value: '', label: "Don't reuse — use the in-app login" },
  { value: 'chrome', label: 'Chrome' },
  { value: 'firefox', label: 'Firefox' },
  { value: 'edge', label: 'Edge' },
  { value: 'brave', label: 'Brave' },
  { value: 'safari', label: 'Safari (blocked on macOS 15+/26)' },
];

export default function UniversityAccessPanel({ form, savedForm, saving = false, onUpdate }) {
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
  const accessDirty = !savedForm || ['ua_enabled', 'ua_login_url', 'ua_ezproxy_prefix', 'ua_cookie_browser']
    .some((key) => form[key] !== savedForm[key]);
  const canLogin = !browserMissing && !accessDirty && !saving && Boolean(savedForm?.ua_login_url?.trim());

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        For non-arXiv / paywalled papers, the review fleet drives a real browser with your
        institutional session to fetch the PDF. Edit the fields, <strong>Save library access</strong>,
        then log in once; the session persists.
      </p>

      {browserMissing && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
          Browser support not installed — run{' '}
          <span className="font-mono">uv pip install -e '.[browser]' && patchright install chromium</span>.
        </p>
      )}

      <CheckboxField label="Enable browser PDF fetch for the review fleet" checked={form.ua_enabled} onChange={(value) => onUpdate('ua_enabled', value)} />
      <Field kind="select" label="Reuse an existing browser login (skip the in-app sign-in)" value={form.ua_cookie_browser}
        onChange={(value) => onUpdate('ua_cookie_browser', value)} options={COOKIE_BROWSERS}
        hint="Reads that browser's session cookies so a paper you can already open there fetches without a second login. Safari is unreadable on recent macOS (Apple's hardened container) — use Chrome/Firefox. Falls back to the in-app login." />
      <Field label="Library login URL" value={form.ua_login_url} onChange={(value) => onUpdate('ua_login_url', value)}
        placeholder="https://your-library.edu/login" />
      <Field label="EZproxy prefix" value={form.ua_ezproxy_prefix} onChange={(value) => onUpdate('ua_ezproxy_prefix', value)}
        placeholder="https://login.ezproxy.myuni.edu/login?url=" hint="Optional — blank for SSO/OpenAthens." />

      <div className="flex items-center gap-3 flex-wrap">
        <Button
          variant="secondary"
          onClick={() => { if (canLogin) loginMutation.mutate(); }}
          disabled={!canLogin || loginMutation.isPending}
          title={canLogin ? 'Open a browser window to log into your library' : 'Set a login URL (and install the browser extra), Save, then log in'}
        >
          {loginMutation.isPending ? 'Opening…' : 'Log in to library'}
        </Button>
        <span className={`text-sm ${loggedIn ? 'text-emerald-700' : 'text-slate-600'}`}>{loggedIn ? 'Logged in' : 'Not logged in'}</span>
        {(saving || accessDirty) && <span className="text-xs text-slate-600">{saving ? 'Wait for Save to finish before logging in.' : 'Save library access before logging in.'}</span>}
        {loginNote && <span className="text-xs text-slate-600">{loginNote}</span>}
      </div>
    </div>
  );
}

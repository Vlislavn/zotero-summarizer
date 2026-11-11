import { isLocalProvider } from './configForm.js';

export const CONNECTION_STAGES = ['feed', 'deep_review', 'backlog'];
const LABELS = { default: 'Fallback (steps without their own model)', feed: 'New paper screening', backlog: 'AI backlog', deep_review: 'Paper reviews & questions', telegram: 'Telegram reviews' };
export const connectionTaskLabel = task => LABELS[task] || 'Saved task';
// "<provider> · Hosted": the provider without its address (table rows; the address is listed once).
// "connection_f615af" is an internal id; the reader knows the connection by its address.
const hostOf = url => { try { return new URL(url).host || url; } catch { return url; } };
export function providerLabel(provider) {
  if (!provider) return 'Not configured';
  const name = provider.name?.startsWith('connection_') ? (provider.base_url ? hostOf(provider.base_url) : 'Saved connection') : provider.name;
  return `${name} · ${isLocalProvider(provider) ? 'Local' : 'Hosted'}`;
}
export const connectionLabel = provider => provider ? `${providerLabel(provider)} · ${provider.base_url || 'Native Anthropic API'}` : 'Not configured';


import { isLocalProvider } from './configForm.js';

export const CONNECTION_STAGES = ['feed', 'deep_review', 'backlog'];
const LABELS = { default: 'Fallback (steps without their own model)', feed: 'New paper screening', backlog: 'AI backlog', deep_review: 'Paper reviews & questions', telegram: 'Telegram reviews' };
export const connectionTaskLabel = task => LABELS[task] || 'Saved task';
// "kather · Hosted": the provider without its address (table rows; the address is listed once).
export function providerLabel(provider) {
  if (!provider) return 'Not configured';
  const name = provider.name?.startsWith('connection_') ? `Saved connection ${provider.name.slice(-6)}` : provider.name;
  return `${name} · ${isLocalProvider(provider) ? 'Local' : 'Hosted'}`;
}
export const connectionLabel = provider => provider ? `${providerLabel(provider)} · ${provider.base_url || 'Native Anthropic API'}` : 'Not configured';


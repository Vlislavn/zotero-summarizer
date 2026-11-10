// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { editorIdentity, legacySettingsDestination, returnLabel, taskReturnTo, taskUrl } from './settingsNavigation.js';
const location = { pathname: '/weekly', search: '?run=saved-run&keep=one', hash: '#evidence' };
it('task entry and exit preserve unrelated query, saved run and nonlegacy hash', () => {
  expect(taskUrl(location, 'telegram')).toBe('/weekly?run=saved-run&keep=one&task=telegram#evidence');
  expect(taskUrl({ ...location, search: '?run=saved-run&task=telegram&keep=one' }, null)).toBe('/weekly?run=saved-run&keep=one#evidence');
});
it.each([['#ai-models', '/settings?keep=one&task=connection'], ['#rss-feeds', '/today?keep=one&task=rss'],
  ['#telegram-sources', '/weekly?keep=one&task=telegram'], ['#university-access', '/settings?keep=one&task=university'],
  ['#essentials-goals', '/today?keep=one&task=selection'], ['#essentials-zotero-paths', '/settings?keep=one&task=zotero']])(
  'legacy bookmark %s resolves without erasing unrelated query', (hash, expected) => {
    expect(legacySettingsDestination({ pathname: '/settings', search: '?keep=one', hash })).toBe(expected);
  });
it('explicit canonical task wins over a legacy hash, and a same-editor anchor/query change retains identity', () => {
  expect(legacySettingsDestination({ pathname: '/settings', search: '?task=language', hash: '#ai-models' })).toBeNull();
  expect(editorIdentity({ ...location, search: '?run=other&task=telegram', hash: '#other' }))
    .toBe(editorIdentity({ ...location, search: '?run=saved-run&task=telegram' }));
  expect(editorIdentity({ pathname: '/settings', search: '?task=language' })).not.toBe(editorIdentity({ pathname: '/settings', search: '?task=mode' }));
});
it.each(['#ai-models', '#rss-feeds', '#telegram-sources', '#university-access', '#essentials-goals', '#essentials-zotero-paths'])(
  'legacy alias %s and its destination have different mounted editor owners', hash => {
    const alias = { pathname: '/settings', search: '', hash };
    const target = new URL(legacySettingsDestination(alias), window.location.origin);
    expect(editorIdentity(alias)).not.toBe(editorIdentity(target));
  });
it('returns to the exact reading context with auto-run explicitly disabled', () => {
  expect(taskReturnTo({ pathname: '/settings', search: '?task=university', state: { returnTo: '/paper/SYNTHETIC?tab=review&autoRun=true&keep=one#sources' } }))
    .toBe('/paper/SYNTHETIC?tab=review&autoRun=false&keep=one#sources');
});
it('accepts same-origin absolute return and preserves a Weekly run', () => {
  expect(taskReturnTo({ ...location, state: { returnTo: `${window.location.origin}/weekly?run=exact-run#sources` } })).toBe('/weekly?run=exact-run#sources');
});
it.each(['https://outside.invalid/paper/ID', '//outside.invalid/settings', 'javascript:alert(1)', '/api/config', '/not-an-app-route', 42])(
  'rejects non-workspace return %s without navigating or running anything', returnTo => {
    expect(taskReturnTo({ ...location, search: '?run=saved-run&task=telegram', state: { returnTo } })).toBe('/weekly?run=saved-run#evidence');
  });
it.each([['/paper/ID?autoRun=false', 'Back to paper'], ['/weekly?run=one', 'Back to Weekly'], ['/settings', 'Back to Settings']])(
  'return label identifies %s', (path, label) => expect(returnLabel(path)).toBe(label));

const LEGACY = {
  '#ai-models': ['/settings', 'connection'],
  '#rss-feeds': ['/today', 'rss'],
  '#telegram-sources': ['/weekly', 'telegram'],
  '#university-access': ['/settings', 'university'],
  '#essentials-goals': ['/today', 'selection'],
  '#essentials-zotero-paths': ['/settings', 'zotero'],
};

export function taskUrl(location, task, pathname = location.pathname) {
  const params = new URLSearchParams(location.search);
  if (task) params.set('task', task); else params.delete('task');
  return pathname + (params.size ? `?${params}` : '') + (LEGACY[location.hash] ? '' : location.hash || '');
}

export function legacySettingsDestination(location) {
  const target = location.pathname === '/settings' && !new URLSearchParams(location.search).has('task') && LEGACY[location.hash];
  return target ? taskUrl(location, target[1], target[0]) : null;
}

export function editorIdentity(location) {
  return `${location.pathname}?task=${new URLSearchParams(location.search).get('task') || ''}`;
}

export function taskReturnTo(location) {
  const supplied = location.state?.returnTo;
  if (typeof supplied === 'string' && URL.canParse(supplied, window.location.origin)) {
    const target = new URL(supplied, window.location.origin);
    if (target.origin === window.location.origin && ['settings', 'today', 'weekly', 'library', 'paper', 'ops', 'setup'].includes(target.pathname.split('/')[1])) {
      if (target.pathname.startsWith('/paper/')) target.searchParams.set('autoRun', 'false');
      return target.pathname + target.search + target.hash;
    }
  }
  return taskUrl(location, null);
}

export function returnLabel(path) {
  const owner = path.split('?')[0].split('/')[1];
  return { paper: 'Back to paper', today: 'Back to Today', weekly: 'Back to Weekly', ops: 'Back to Ops', library: 'Back to Library', setup: 'Back to Setup' }[owner] || 'Back to Settings';
}

// "92" -> "1m 32s". Live elapsed/ETA for the auto-generating review.
export function fmt(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

// Truthy when the cached review has something to show (kept as-is, not coerced: callers render it).
export function reviewedValue(deep) {
  return deep && !deep.needs_pdf && (deep.digest || deep.quality || (deep.goal_summaries || []).length);
}

// The digest carries at least one readable summary/finding.
export function hasUsableDigest(deep) {
  const digest = deep?.digest;
  return !deep?.needs_pdf && !deep?.error && [
    digest?.tldr, digest?.executive_summary,
    ...(Array.isArray(digest?.key_findings) ? digest.key_findings : []),
  ].some((value) => String(value || '').trim());
}

export function hasSectionMap(overlay) {
  return Boolean(overlay && !overlay.degraded && (overlay.sections || []).length);
}

// The closed Figures row: what the build produced, in one line.
export function figuresSummary(render, completed) {
  if (render?.stale) return 'Outdated — open to rebuild';
  if (render?.status === 'running') return 'Building from the PDF…';
  const n = (render?.figures || []).filter((f) => f && f.name).length;
  return completed && n ? `${n === 1 ? '1 figure or table' : `${n} figures and tables`} from the PDF` : 'Extracted from the PDF';
}

// Sticky table of contents: only the sections the page renders.
export function buildToc({ showFigures, hasMap, abstractText }) {
  return [
    { id: 'review', label: 'Review' },
    { id: 'similar', label: 'Similar papers' },
    showFigures && { id: 'figures', label: 'Figures' },
    hasMap && { id: 'paper-map', label: 'Paper map' },
    abstractText && { id: 'abstract', label: 'Abstract' },
  ].filter(Boolean);
}

// A feed paper whose PDF the app already holds (no Zotero attachment) can still build figures.
function figuresBuildable(detail) {
  return Boolean(detail?.has_pdf) || Boolean(detail?.pdf_held);
}

// Props for the Figures fold, or null when the fold is hidden.
export function figuresProps({ detail, render, runner, attach }) {
  const buildable = figuresBuildable(detail);
  const renderCompleted = render?.status === 'completed';
  const show = runner.online && runner.serverAvailable && (buildable || renderCompleted || render?.status === 'running');
  return show ? { render, renderCompleted, figuresBuildable: buildable, attach } : null;
}

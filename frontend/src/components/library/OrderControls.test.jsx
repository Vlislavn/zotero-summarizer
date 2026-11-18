// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import ReadNextView from './ReadNextView.testkit.jsx';
import useLibraryFilters from '../../hooks/useLibraryFilters.js';
import { BALANCED_ORDER, selectPreset } from '../../utils/readNextOrder.js';

vi.mock('./InlineAnnotate.jsx', () => ({ default: () => null }));
vi.mock('../../hooks/usePaperReview.js', () => ({ default: () => ({ detail: null }) }));
afterEach(cleanup);

const full = { present: 3, of: 3 };
const META = {
  coverage: { relevance: full, goals: full, quality: full, prestige: full },
  fill: {}, balancedWeights: { relevance: 0.45, goals: 0.4, prestige: 0.15 },
};
const ITEMS = [{ item_key: 'K1', title: 'First paper', authors: 'Ada', relevance_score: 4.5 }];

// The page's wiring in miniature: URL state from the real hook, the order kept locally.
function Page({ orderMeta = META, ...view }) {
  const f = useLibraryFilters();
  const [order, setOrder] = useState(BALANCED_ORDER);
  return (
    <>
      <output data-testid="query">{useLocation().search}</output>
      <ReadNextView
        items={ITEMS} loading={false} err={null} modelReady selectMode={false} onToggleSelectMode={vi.fn()}
        selected={new Set()} onToggleItem={vi.fn()} onRunTriage={vi.fn()} onSaved={vi.fn()} starting={false}
        sort={f.sort} onSortChange={f.setSort} order={order} onOrderChange={setOrder} orderMeta={orderMeta}
        {...view}
      />
    </>
  );
}
const show = (props, url = '/library') => render(<MemoryRouter initialEntries={[url]}><Page {...props} /></MemoryRouter>);
const pick = (value) => fireEvent.change(screen.getByRole('combobox', { name: 'Order' }), { target: { value } });
const query = () => screen.getByTestId('query').textContent;
const labels = (group) => within(screen.getByRole('group', { name: group })).getAllByRole('option').map((o) => o.textContent);

describe('Order on a phone', () => {
  it('keeps the shares caption behind ⓘ, in one row with Order, and says it on request', () => {
    vi.stubGlobal('matchMedia', (query) => ({ matches: query.includes('max-width'), media: query, addEventListener() {}, removeEventListener() {} }));
    try {
      show();
      expect(screen.queryByTestId('order-caption')).toBeNull();
      expect(screen.getByRole('combobox', { name: 'Order' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'What does Order change?' }));
      const note = screen.getByRole('note');
      expect(within(note).getByTestId('order-caption').textContent).toBe('Balanced · relevance 45% · goals 40% · prestige 15%');
      expect(note.textContent).toMatch(/★ band/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('one Order list', () => {
  it('replaces Sort by + Order: presets and Zotero fields in one select, one caption, ★ legend inside ⓘ', () => {
    show();
    expect(screen.queryByRole('combobox', { name: 'Sort by' })).toBeNull();
    expect(screen.getAllByRole('combobox', { name: 'Order' })).toHaveLength(1);
    expect(labels('Smart order')).toEqual(['Balanced', 'Relevance', 'My goals', 'Rigor first', 'Custom']);
    expect(labels('Zotero fields')).toEqual(['Title', 'Creator', 'Publication', 'Year', 'Date Added']);
    expect(screen.getAllByTestId('order-caption')).toHaveLength(1);
    expect(screen.getByTestId('order-caption').textContent).toBe('Balanced · relevance 45% · goals 40% · prestige 15%');
    expect(screen.queryByRole('note')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'What does Order change?' }));
    expect(screen.getByRole('note').textContent).toMatch(/★ band/);
  });

  it('a field hides the preset shares and writes s / sd; a preset takes the list back', () => {
    show();
    pick('field:year');
    expect(screen.queryByTestId('order-caption')).toBeNull();
    expect(query()).toBe('?s=year');                       // year descends by default: no sd
    fireEvent.click(screen.getByRole('button', { name: 'Toggle sort direction' }));
    expect(query()).toBe('?s=year&sd=asc');
    pick('field:title');
    expect(query()).toBe('?s=title');
    pick('preset:quality');
    expect(query()).toBe('');
    expect(screen.getByTestId('order-caption').textContent).toMatch(/^Rigor first · /);
    expect(screen.queryByRole('button', { name: 'Toggle sort direction' })).toBeNull();
  });

  it('restores a field from an old ?s=&sd= link', () => {
    show({}, '/library?s=creator&sd=desc');
    expect(screen.getByRole('combobox', { name: 'Order' }).value).toBe('field:creator');
    expect(screen.getByRole('button', { name: 'Toggle sort direction' }).textContent).toBe('↓');
  });

  it.each([
    ['during a search', { searchQuery: 'agents', semantic: true, orderMeta: null }, 'Presets are off during a search'],
    ['offline', { offline: true, orderMeta: null }, 'Presets need the live list — you are offline'],
  ])('%s the presets are disabled and say why; the fields still work', (_, props, reason) => {
    show(props);
    const select = screen.getByRole('combobox', { name: 'Order' });
    expect(screen.getByText(reason)).toBeTruthy();
    expect(select.getAttribute('aria-describedby')).toBe(screen.getByText(reason).id);
    expect(select.value).toBe('best');
    expect(within(screen.getByRole('group', { name: 'Smart order' })).getAllByRole('option').every((o) => o.disabled)).toBe(true);
    pick('field:title');
    expect(query()).toBe('?s=title');
    pick('best');
    expect(query()).toBe('');
  });

  it('Custom starts from the weights of the order it replaces (unchanged formulas)', () => {
    expect(selectPreset(BALANCED_ORDER, 'custom', META).weights).toEqual({ relevance: 4.5, goals: 4, quality: 0, prestige: 1.5 });
  });

  it('the list has no second «Show already-read» checkbox (it lives in the Filter sheet)', () => {
    show();
    expect(screen.queryByRole('checkbox', { name: /already-read|Show read/ })).toBeNull();
  });
});

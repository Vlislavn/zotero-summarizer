// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('./InlineAnnotate.jsx', () => ({
  default: ({ itemKey }) => <div data-testid="inline-annotate">Detail {itemKey}</div>,
}));

import ReadNextView from './ReadNextView.testkit.jsx';

function setViewport({ desktop }) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query) => ({
      matches: desktop,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  });
}

const items = [
  { item_key: 'K1', title: 'First paper', authors: 'Ada', has_pdf: true, relevance_score: 4.5 },
  { item_key: 'K2', title: 'Second paper', authors: 'Grace', has_pdf: false, relevance_score: 4.1 },
  { item_key: 'K3', title: 'Third paper', authors: 'Third Author', has_pdf: true, relevance_score: 3.9 },
];

function renderView(props = {}, entry = '/library') {
  return render(
    <MemoryRouter initialEntries={[entry]}><ReadNextView
      items={items}
      loading={false}
      err={null}
      includeRead={false}
      onToggleIncludeRead={vi.fn()}
      readHidden={0}
      totalUnread={items.length}
      onSaved={vi.fn()}
      status="ready"
      modelReady
      error={null}
      computedAt={null}
      scoresStale={false}
      distribution={null}
      onRescore={vi.fn()}
      selectMode={false}
      onToggleSelectMode={vi.fn()}
      selected={new Set()}
      onToggleItem={vi.fn()}
      onRunTriage={vi.fn()}
      starting={false}
      {...props}
    /></MemoryRouter>,
  );
}
const where = () => screen.getByTestId('location').textContent;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('localStorage', {
    getItem: vi.fn(() => ''),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
  Element.prototype.scrollIntoView = vi.fn();
  setViewport({ desktop: false });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ReadNextView detail', () => {
  it('offers Rescore after a queue read fails even if the retained rows were fully scored', () => {
    const onRescore = vi.fn();
    const err = Object.assign(new Error('Reading cache is corrupt'), { status: 500 });
    renderView({ computedAt: '2026-09-06T12:00:00Z', err, onRescore });

    expect(screen.getByText(/Failed to load queue:/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Rescore' }));

    expect(onRescore).toHaveBeenCalledTimes(1);
    expect(screen.getByText('First paper')).toBeTruthy();
  });

  it('opens the tapped paper in a detail over the list, which stays mounted', () => {
    renderView();

    fireEvent.click(screen.getByText('Second paper'));

    const dialog = screen.getByRole('dialog', { name: 'Second paper' });
    expect(within(dialog).getByTestId('inline-annotate').textContent).toContain('K2');
    expect(where()).toBe('/library?open=K2');
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toHaveLength(3);
    expect(screen.getByText('First paper')).toBeTruthy();
    expect(screen.getByText('Third paper')).toBeTruthy();
  });

  it('puts the detail beside the list on a wide screen, both interactive', () => {
    setViewport({ desktop: true });
    renderView();

    fireEvent.click(screen.getByText('First paper'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(within(screen.getByTestId('library-detail')).getByTestId('inline-annotate').textContent).toContain('K1');

    fireEvent.click(screen.getByText('Third paper'));  // the list is not blocked: another row replaces the detail
    expect(screen.getByTestId('inline-annotate').textContent).toContain('K3');
    expect(where()).toBe('/library?open=K3');
    fireEvent.click(screen.getByRole('button', { name: 'Close detail' }));
    expect(screen.queryByTestId('library-detail')).toBeNull();
  });

  it('opened from a link: closing keeps the page and only drops `open`', () => {
    renderView({}, '/library?c=COLL&open=K2');

    expect(screen.getByRole('dialog', { name: 'Second paper' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(where()).toBe('/library?c=COLL');
  });

  it('closing after a tap goes back to the list entry; stepping does not add history entries', () => {
    renderView({}, '/library?c=COLL');

    fireEvent.click(screen.getByText('First paper'));
    expect(screen.getByText('1 of 3')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Next paper' }));
    expect(where()).toBe('/library?c=COLL&open=K2');
    expect(screen.getByText('2 of 3')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(where()).toBe('/library?c=COLL');
  });

  it('shows a paper that is not in the list without ‹ › and says so', () => {
    renderView({}, '/library?open=GONE');

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Not in the current list')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: /Previous paper|Next paper/ })).toBeNull();
    expect(within(dialog).getByTestId('inline-annotate').textContent).toContain('GONE');
  });

  it('on close brings the paper’s row back: rendered past the first 60, scrolled to, focused', () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ item_key: `R${i}`, title: `Row paper ${i}`, relevance_score: 3 }));
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ top: 5000, bottom: 5040 });
    renderView({ items: many, totalUnread: 100 }, '/library?open=R90');
    expect(screen.queryByText('Row paper 90', { selector: 'span' })).toBeNull();  // the list reveals 60 rows at first

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    const row = screen.getByText('Row paper 90', { selector: 'span' }).closest('li');
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView.mock.contexts).toContain(row);
    expect(document.activeElement).toBe(row.querySelector('button'));
  });

  it('opens a decided paper that sits behind «Decided by you» and unfolds it on close', () => {
    const decided = { item_key: 'D1', title: 'Decided one', decided: true, user_priority: 'should_read' };
    renderView({ items: [...items, decided] }, '/library?open=D1');
    expect(screen.queryByText('Decided one', { selector: 'span' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(screen.getByText('Decided one', { selector: 'span' })).toBeTruthy();
  });

  it('in Select mode a tap ticks the row and opens nothing', () => {
    const onToggleItem = vi.fn();
    renderView({ selectMode: true, onToggleItem });

    fireEvent.click(screen.getByText('Second paper'));

    expect(onToggleItem).toHaveBeenCalledWith('K2');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(where()).toBe('/library');
  });

  it('says whether a paper has a PDF in its own row, with no brief button beside it', () => {
    renderView();

    const rows = screen.getAllByRole('listitem');
    expect(within(rows[0]).getByText('PDF')).toBeTruthy();
    expect(within(rows[1]).queryByText('PDF')).toBeNull();
    expect(screen.queryByRole('button', { name: /brief|Open cached review/i })).toBeNull();
  });

  it('shows no empty state when the queue failed to load and there are no rows', () => {
    const err = Object.assign(new Error('boom'), { status: 500 });
    renderView({ items: [], rawCount: 0, totalUnread: 0, err });

    expect(screen.getByText(/Failed to load queue:/)).toBeTruthy();
    expect(screen.queryByText(/Nothing to read/)).toBeNull();
  });

});

describe('ReadNextView Goals strong', () => {
  const rows = [
    { item_key: 'G1', title: 'Close to goals', goal_sim: 0.9, relevance_score: 3 },
    { item_key: 'G2', title: 'Middle', goal_sim: 0.5, relevance_score: 3 },
    { item_key: 'G3', title: 'Far', goal_sim: 0.1, relevance_score: 3 },
  ];

  it('marks the top of the list on the full list, and nowhere under a scope, a search or offline', () => {
    renderView({ items: rows, cohortFull: true });
    const [close, , far] = screen.getAllByRole('listitem');
    expect(within(close).getByText('Goals strong')).toBeTruthy();
    expect(within(far).queryByText('Goals strong')).toBeNull();
    cleanup();

    renderView({ items: rows });  // a collection / tag is applied: cohortFull is false
    expect(screen.queryByText('Goals strong')).toBeNull();
    cleanup();
    renderView({ items: rows, cohortFull: true, searchQuery: 'agents', semantic: true });
    expect(screen.queryByText('Goals strong')).toBeNull();
    cleanup();
    renderView({ items: rows, cohortFull: true, offline: true, status: 'offline' });
    expect(screen.queryByText('Goals strong')).toBeNull();
  });
});

describe('ReadNextView groups', () => {
  const must = (n) => Array.from({ length: n }, (_, i) => (
    { item_key: `M${i}`, title: `Must paper ${i}`, decided: true, user_priority: 'must_read', relevance_score: 4 }
  ));
  const decidedRows = [
    { item_key: 'D1', title: 'Decided one', decided: true, user_priority: 'should_read', relevance_score: 3 },
    { item_key: 'D2', title: 'Decided two', decided: true, user_priority: 'could_read', relevance_score: 2.5 },
  ];

  it('shows 3 of 23 Must read rows and one control for the rest', () => {
    renderView({ items: [...must(23), ...items] });

    expect(screen.getByRole('heading', { name: 'Your must read · 23' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'To decide · 3' })).toBeTruthy();
    expect(screen.getByText('Must paper 2')).toBeTruthy();
    expect(screen.queryByText('Must paper 3')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show all 23' }));
    expect(screen.getByText('Must paper 22')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(screen.queryByText('Must paper 3')).toBeNull();
    // The old counts sentence and the repeated «You: must read» are gone.
    expect(screen.queryByText('About these counts')).toBeNull();
    expect(screen.queryByText(/You: Must read/)).toBeNull();
  });

  it('keeps decided papers behind one «Decided by you · N · Show» press', () => {
    renderView({ items: [...items, ...decidedRows] });

    expect(screen.queryByText('Decided one')).toBeNull();
    const toggle = screen.getByRole('button', { name: 'Decided by you · 2 · Show' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);

    expect(screen.getByText('Decided one')).toBeTruthy();
    expect(screen.getByText('Decided two')).toBeTruthy();
    expect(screen.getByText('You: Should read')).toBeTruthy();
    expect(screen.queryByText('AI summary:')).toBeNull();  // decided rows are compact
    expect(screen.getByRole('button', { name: 'Decided by you · 2 · Hide' }).getAttribute('aria-expanded')).toBe('true');
  });

  it('groups an offline working set (no `decided`, no counts) by each row’s own verdict', () => {
    const offlineRows = [
      { item_key: 'O1', title: 'Offline undecided', user_priority: null },
      { item_key: 'O2', title: 'Offline must', user_priority: 'must_read' },
      { item_key: 'O3', title: 'Offline should', user_priority: 'should_read' },
    ];
    renderView({ items: offlineRows, offline: true, status: 'offline', modelReady: false });

    // Must lead even though the cached order has it second; no live numbers are printed.
    expect(screen.getByRole('heading', { name: 'Your must read' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'To decide' })).toBeTruthy();
    const titles = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(titles[0]).toContain('Offline must');
    expect(titles[1]).toContain('Offline undecided');
    fireEvent.click(screen.getByRole('button', { name: 'Decided by you · Show' }));
    expect(screen.getByText('Offline should')).toBeTruthy();
  });
});

describe('proposal chip', () => {
  const proposal = { proposed: 'should_read', confidence: 0.85, flags: [] };

  it('shows «Review suggests» with its confidence and no Confirm / Override buttons', () => {
    renderView({ items: [{ ...items[0], proposed_verdict: proposal }, items[1]] });

    expect(screen.getByText('◇ Review suggests: Should read · 85%')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Confirm|Override/ })).toBeNull();
    expect(screen.queryByText(/not used for rank/i)).toBeNull();
  });

  it('says «flagged» instead of the percentage when the review raised a flag', () => {
    renderView({
      items: [{ ...items[0], proposed_verdict: { proposed: 'could_read', confidence: 0.4, flags: ['overstated'] } }],
    });

    expect(screen.getByText('◇ Review suggests: Could read · flagged')).toBeTruthy();
  });

  it('is hidden for a row the user already labelled', () => {
    renderView({
      items: [{ ...items[0], decided: true, user_priority: 'should_read', proposed_verdict: proposal }],
    });

    expect(screen.queryByText(/Review suggests/)).toBeNull();
  });
});

describe('ReadNextView meaning-search caption', () => {
  const fifty = Array.from({ length: 50 }, (_, i) => ({ item_key: `S${i}`, title: `Hit ${i}`, relevance_score: 3 }));

  it('says Top 50 by meaning when 50 rows come back, and N of 50 under filters', () => {
    renderView({ items: fifty, rawCount: 50, semantic: true, searchQuery: 'agents' });
    expect(screen.getByTestId('search-caption').textContent).toBe('· Top 50 by meaning for “agents”');
    cleanup();

    renderView({ items: fifty.slice(0, 7), rawCount: 50, hasActiveFilters: true, semantic: true, searchQuery: 'agents' });
    expect(screen.getByTestId('search-caption').textContent)
      .toBe('· Top 50 by meaning for “agents” · 7 of 50 match filters');
  });

  it('shows no caption without a search', () => {
    renderView({ items: fifty, rawCount: 50 });
    expect(screen.queryByTestId('search-caption')).toBeNull();
    expect(screen.queryByText(/Top 50 by meaning/)).toBeNull();
  });
});

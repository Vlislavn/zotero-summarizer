// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LibraryFilters from './LibraryFilterSheet.jsx';
import useLibraryFilters from '../../hooks/useLibraryFilters.js';
import { buildPredicate, goalHighKeys } from '../../utils/relevanceBands.js';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const QUEUE = [
  { item_key: 'A', title: 'Alpha', relevance_score: 4.8, quality_grade: 'A', has_code: true, goal_sim: 0.9, venue: 'Nature' },
  { item_key: 'B', title: 'Bravo', relevance_score: 2.5, quality_grade: 'D', has_code: false, goal_sim: 0.1 },
  { item_key: 'C', title: 'Charlie', relevance_score: 2.7, quality_grade: null, has_code: null },
  { item_key: 'D', title: 'Delta', relevance_score: 3.6, has_code: null },
];
const COLLECTIONS = [
  { key: 'K1', label: 'Inbox', item_count: 40, depth: 0 }, { key: 'K2', label: 'Reading', item_count: 9, depth: 0 },
];
const TAGS = [{ tag: 'method', item_count: 5 }, { tag: '🧪 lab', item_count: 3 }, { tag: 'agents', item_count: 8 }];

function Page({ onScope = () => {}, modelReady = true, readHidden = 7 }) {
  const f = useLibraryFilters({ onScopeChange: onScope });
  const ctx = { goalHigh: goalHighKeys(QUEUE), prestigeFloor: null };
  const shown = QUEUE.filter(buildPredicate(f.clientFilters, ctx));
  return (
    <>
      <ul aria-label="list">{shown.map((r) => <li key={r.item_key}>{r.title}</li>)}</ul>
      <output data-testid="scope">{`${f.selectedCollection}|${f.selectedTag}|${f.includeRead}`}</output>
      <LibraryFilters f={f} collections={COLLECTIONS} tags={TAGS} queue={QUEUE} ctx={ctx} modelReady={modelReady}
        serverAvailable readCount={readHidden} matchCount={shown.length} loading={false} />
    </>
  );
}
const openSheet = (props) => {
  render(<MemoryRouter><Page {...props} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: /^Filter/ }));
  return screen.getByRole('dialog', { name: 'Filter' });
};
const titles = () => within(screen.getByRole('list', { name: 'list', hidden: true })).getAllByRole('listitem', { hidden: true }).map((li) => li.textContent);

it('Rigor does not hide the unreviewed papers silently: a Not reviewed bucket carries their count', () => {
  const dialog = openSheet();
  const rigor = within(within(dialog).getByRole('group', { name: 'Rigor' }));
  expect(rigor.getByRole('button', { name: 'A/B 1' })).toBeTruthy();
  expect(rigor.getByRole('button', { name: 'C/D 1' })).toBeTruthy();
  expect(rigor.getByRole('button', { name: 'Not reviewed 2' })).toBeTruthy();   // 1 + 1 + 2 = every row

  fireEvent.click(rigor.getByRole('button', { name: 'A/B 1' }));
  expect(titles()).toEqual(['Alpha']);
  fireEvent.click(rigor.getByRole('button', { name: 'A/B 1' }));
  fireEvent.click(rigor.getByRole('button', { name: 'Not reviewed 2' }));
  expect(titles()).toEqual(['Charlie', 'Delta']);
});

it('Code has Found, No link and Not reviewed buckets with their counts', () => {
  const dialog = openSheet();
  const code = within(within(dialog).getByRole('group', { name: 'Code' }));
  fireEvent.click(code.getByRole('button', { name: 'Found 1' }));
  expect(titles()).toEqual(['Alpha']);
  fireEvent.click(code.getByRole('button', { name: 'Found 1' }));
  fireEvent.click(code.getByRole('button', { name: 'No link 1' }));
  expect(titles()).toEqual(['Bravo']);
  expect(code.getByRole('button', { name: 'Not reviewed 2' })).toBeTruthy();
});

it('a collection or a tag applies the moment it is picked, and the list has its own search with emoji tags first', () => {
  const onScope = vi.fn();
  const dialog = openSheet({ onScope });
  const tagList = within(within(dialog).getByRole('radiogroup', { name: 'Tag' }));
  expect(tagList.getAllByRole('radio').map((r) => r.closest('label').textContent))
    .toEqual(['All tags', '🧪 lab3', 'method5', 'agents8']);

  fireEvent.change(within(dialog).getByRole('searchbox', { name: 'Search tags' }), { target: { value: 'ag' } });
  expect(tagList.getAllByRole('radio').map((r) => r.closest('label').textContent)).toEqual(['All tags', 'agents8']);
  fireEvent.click(tagList.getByRole('radio', { name: /agents/ }));
  fireEvent.click(within(dialog).getByRole('radio', { name: /Reading/ }));

  expect(screen.getByTestId('scope').textContent).toBe('K2|agents|false');   // no «Show» press needed
  expect(onScope).toHaveBeenCalledTimes(2);
});

it('Show papers carries a count only after a client-side facet changed', () => {
  const dialog = openSheet();
  expect(within(dialog).getByRole('button', { name: 'Show papers' })).toBeTruthy();

  fireEvent.click(within(dialog).getByRole('radio', { name: /Inbox/ }));      // server scope: count unknown
  expect(within(dialog).getByRole('button', { name: 'Show papers' })).toBeTruthy();

  fireEvent.click(within(dialog).getByRole('button', { name: 'Found 1' }));
  expect(within(dialog).getByRole('button', { name: 'Show 1 paper' })).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Found 1' }));   // back to where it opened
  expect(within(dialog).getByRole('button', { name: 'Show papers' })).toBeTruthy();
});

it('Show read papers carries the read count and applies at once; the button counts the facets in force', () => {
  const dialog = openSheet({ readHidden: 7 });
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Show read papers (7)' }));
  expect(screen.getByTestId('scope').textContent).toBe('||true');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Show papers' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByRole('button', { name: 'Filter 1' })).toBeTruthy();
});

it('More holds Prestige; without a ready model only the scope facets show', () => {
  let dialog = openSheet();
  expect(within(dialog).getByText('More')).toBeTruthy();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Show papers' }));
  cleanup();
  dialog = openSheet({ modelReady: false });
  expect(within(dialog).queryByRole('group', { name: 'Rigor' })).toBeNull();
  expect(within(dialog).getByRole('radiogroup', { name: 'Tag' })).toBeTruthy();
});

it('is a full-screen sheet on phones and the side panel from 1024 px', () => {
  expect(openSheet().getAttribute('data-variant')).toBe('full');
  cleanup();
  vi.stubGlobal('matchMedia', (query) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
  expect(openSheet().getAttribute('data-variant')).toBe('side');
});

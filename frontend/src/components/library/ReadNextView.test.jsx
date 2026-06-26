// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';

vi.mock('./InlineAnnotate.jsx', () => ({
  default: ({ itemKey }) => <div data-testid="inline-annotate">Detail {itemKey}</div>,
}));

vi.mock('./OpenBriefButton.jsx', () => ({
  default: ({ itemKey, hasPdf }) => (
    <button type="button" data-testid={`brief-${itemKey}`} data-has-pdf={String(hasPdf)}>
      brief
    </button>
  ),
}));

import ReadNextView from './ReadNextView.jsx';

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
  { item_key: 'K3', title: 'Third paper', authors: 'Katherine', has_pdf: true, relevance_score: 3.9 },
];

function renderView(props = {}) {
  return render(
    <ReadNextView
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
    />,
  );
}

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

describe('ReadNextView mobile expansion', () => {
  it('renders the expanded detail inside the tapped row on mobile', () => {
    renderView();

    fireEvent.click(screen.getByText('Second paper'));

    const rows = screen.getAllByRole('listitem');
    expect(within(rows[1]).getByTestId('expanded-paper-panel')).toBeTruthy();
    expect(within(rows[1]).getByTestId('inline-annotate').textContent).toContain('K2');
    expect(within(rows[2]).queryByTestId('expanded-paper-panel')).toBeNull();
  });

  it('passes PDF availability to the one-click brief control', () => {
    renderView();

    expect(screen.getByTestId('brief-K1').dataset.hasPdf).toBe('true');
    expect(screen.getByTestId('brief-K2').dataset.hasPdf).toBe('false');
  });
});

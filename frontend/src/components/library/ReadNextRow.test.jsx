// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import ReadNextRow from './ReadNextRow.jsx';

afterEach(cleanup);

const base = { item_key: 'K1', title: 'Agentic science', authors: 'Guo; Li', has_pdf: true, relevance_score: 3.6 };
const reviewed = {
  ...base,
  venue: 'Nature',
  year: '2026',
  review_digest: { tldr: 'Agents plan and run experiments end to end.' },
  quality_grade: 'A',
  quality_band: 'highlight',
  read_decision: 'skim',
  estimated_read_minutes: 8,
  has_code: true,
};

const renderRow = (item, props = {}) => render(
  <ReadNextRow item={item} onToggleOpen={vi.fn()} onToggleSelect={vi.fn()} {...props} />,
);

describe('ReadNextRow', () => {
  it('shows a reviewed paper as title, signal line, AI summary and footer', () => {
    renderRow(reviewed, { goalsStrong: true });

    expect(screen.getByText('Agentic science')).toBeTruthy();
    const line = screen.getByText('★ 3.6').parentElement;
    expect([...line.children].map((c) => c.textContent)).toEqual(['★ 3.6', 'Goals strong', 'Rigor A', 'Code', 'Skim 8 min']);
    expect(screen.getByText('AI summary:')).toBeTruthy();
    expect(screen.getByText(/Agents plan and run experiments/)).toBeTruthy();
    const footer = screen.getByText('Guo; Li · Nature').parentElement.parentElement;
    expect(footer.textContent).toBe('Guo; Li · Nature·2026·PDF');
    expect(screen.queryByText('Not reviewed')).toBeNull();
  });

  it('falls back to the abstract clip, without the AI label, and says Not reviewed', () => {
    renderRow({ ...base, abstract_preview: '<p>Recent systems promise discovery.</p>' });

    expect(screen.getByText('Recent systems promise discovery.')).toBeTruthy();
    expect(screen.queryByText(/AI summary/)).toBeNull();
    expect(screen.getByText('Not reviewed')).toBeTruthy();
    expect(screen.queryByText(/Code|min$/)).toBeNull();
  });

  it('renders an offline row that has none of the new fields', () => {
    renderRow({ item_key: 'O1', title: 'Offline paper' }, { modelReady: false });

    expect(screen.getByText('Offline paper')).toBeTruthy();
    expect(screen.getByText('Not reviewed')).toBeTruthy();
    expect(screen.queryByText(/not scored yet|★/)).toBeNull();
  });

  it('keeps a decided row compact: title, signals and your verdict — no summary, no footer', () => {
    renderRow({ ...reviewed, decided: true, user_priority: 'should_read' });

    expect(screen.getByText('You: Should read')).toBeTruthy();
    expect(screen.getByText('Rigor A')).toBeTruthy();
    expect(screen.queryByText(/AI summary/)).toBeNull();
    expect(screen.queryByText('PDF')).toBeNull();
    expect(screen.queryByText(/Guo/)).toBeNull();
  });

  it('names a Not interested verdict, and says nothing extra inside the Must group', () => {
    const { unmount } = renderRow({ ...base, decided: true, user_priority: 'dont_read' });
    expect(screen.getByText('You: Not interested')).toBeTruthy();
    unmount();
    renderRow({ ...base, decided: true, user_priority: 'must_read' }, { ownChip: false });
    expect(screen.queryByText(/You:/)).toBeNull();
  });

  it('words Rigor A by what backs it: flagged, highlight, or checklist only', () => {
    const rigor = (band) => {
      const { unmount } = renderRow({ ...base, quality_grade: 'A', quality_band: band });
      const text = screen.getByText(/^Rigor/).textContent;
      unmount();
      return text;
    };
    expect(rigor('flag')).toBe('Rigor A · flagged');
    expect(rigor('neutral')).toBe('Rigor A · checklist only');
    expect(rigor('uncertain')).toBe('Rigor A · checklist only');
    expect(rigor('highlight')).toBe('Rigor A');
    const { unmount } = renderRow({ ...base, quality_grade: 'C', quality_band: 'neutral' });
    expect(screen.getByText('Rigor C')).toBeTruthy();
    unmount();
    renderRow({ ...base, quality_grade: 'D', quality_band: 'flag' });
    expect(screen.getByText('Rigor D · flagged')).toBeTruthy();
  });

  it('shows only the suggestion on an undecided row, with no value held in a tooltip', () => {
    const { container } = renderRow({
      ...base, proposed_verdict: { proposed: 'should_read', confidence: 0.7, flags: ['red_flags'] },
    });

    expect(screen.getByText('◇ Review suggests: Should read · flagged')).toBeTruthy();
    expect(container.querySelector('[title]')).toBeNull();
  });

  it('opens on tap and toggles the checkbox only in Select mode', () => {
    const onToggleOpen = vi.fn();
    const onToggleSelect = vi.fn();
    const { rerender } = renderRow(base, { onToggleOpen, onToggleSelect });
    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Agentic science/ }));
    expect(onToggleOpen).toHaveBeenCalledWith('K1');

    rerender(<ReadNextRow item={base} selectMode checked onToggleOpen={onToggleOpen} onToggleSelect={onToggleSelect} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Agentic science' }));
    expect(onToggleSelect).toHaveBeenCalledWith('K1');
  });
});

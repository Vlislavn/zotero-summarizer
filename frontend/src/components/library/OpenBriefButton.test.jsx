// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

vi.mock('../../api/libraryApi.js', () => ({
  fetchPaperRender: vi.fn(),
  buildPaperRender: vi.fn(),
}));

import { fetchPaperRender, buildPaperRender } from '../../api/libraryApi.js';
import OpenBriefButton from './OpenBriefButton.jsx';

let tab;
beforeEach(() => {
  vi.clearAllMocks();
  // A fake new-tab handle: our code writes a holding page, nulls opener, then
  // navigates via location.href (plain object setter is enough to assert on).
  tab = { opener: {}, document: { write: vi.fn() }, location: { href: '' }, close: vi.fn() };
  vi.spyOn(window, 'open').mockReturnValue(tab);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const clickBtn = () => fireEvent.click(screen.getByRole('button', { name: 'Open full review' }));

describe('OpenBriefButton', () => {
  it('tries full-text acquisition before opening a no-local-PDF review', async () => {
    fetchPaperRender
      .mockResolvedValueOnce({ status: 'missing', needs_pdf: true })
      .mockResolvedValue({ status: 'completed', built_at: 'V0' });
    buildPaperRender.mockResolvedValue({ status: 'running' });
    render(<OpenBriefButton itemKey="K0" hasPdf={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fetch and open full review' }));
    await waitFor(() => expect(buildPaperRender).toHaveBeenCalledWith('K0', { allowAcquireMissing: true }));
    await waitFor(
      () => expect(tab.location.href).toBe('/paper/K0'),
      { timeout: 4000 },
    );
  });

  it('opens an already-built review artifact without triggering a build', async () => {
    fetchPaperRender.mockResolvedValue({ status: 'completed', built_at: 'V1' });
    render(<OpenBriefButton itemKey="K1" />);
    clickBtn();
    await waitFor(() => expect(tab.location.href).toBe('/paper/K1'));
    expect(window.open).toHaveBeenCalledWith('about:blank', '_blank');
    expect(buildPaperRender).not.toHaveBeenCalled();
  });

  it('builds on demand, polls, then opens the review route', async () => {
    fetchPaperRender
      .mockResolvedValueOnce({ status: 'missing' })   // initial: nothing built
      .mockResolvedValue({ status: 'completed', built_at: 'V2' }); // after build
    buildPaperRender.mockResolvedValue({ status: 'running' });
    render(<OpenBriefButton itemKey="K2" />);
    clickBtn();
    await waitFor(() => expect(buildPaperRender).toHaveBeenCalledWith('K2', { allowAcquireMissing: false }));
    await waitFor(
      () => expect(tab.location.href).toBe('/paper/K2'),
      { timeout: 4000 },
    );
  });

  it('labeled variant renders the CTA text and drives the same build+open path', async () => {
    fetchPaperRender.mockResolvedValue({ status: 'completed', built_at: 'V4' });
    render(<OpenBriefButton itemKey="K4" label="Open full review ↗" />);
    fireEvent.click(screen.getByRole('button', { name: 'Open full review ↗' }));
    await waitFor(() => expect(tab.location.href).toBe('/paper/K4'));
    expect(buildPaperRender).not.toHaveBeenCalled();
  });

  it('does not re-trigger a build when one is already running', async () => {
    fetchPaperRender
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValue({ status: 'completed', built_at: 'V3' });
    render(<OpenBriefButton itemKey="K3" />);
    clickBtn();
    await waitFor(
      () => expect(tab.location.href).toBe('/paper/K3'),
      { timeout: 4000 },
    );
    expect(buildPaperRender).not.toHaveBeenCalled();
  });
});

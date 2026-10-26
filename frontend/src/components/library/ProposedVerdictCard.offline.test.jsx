// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ProposedVerdictCard from './ProposedVerdictCard.jsx';
import { submitVerdict } from '../../api/goldenApi.js';
import { queueRejectTag } from '../../api/libraryApi.js';
vi.mock('../../api/goldenApi.js', () => ({ submitVerdict: vi.fn() }));
vi.mock('../../api/libraryApi.js', () => ({ queueRejectTag: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it('confirms a durable offline rejection without attempting a native tag request', async () => {
  submitVerdict.mockResolvedValue({ saved_offline: true, queued: true });
  queueRejectTag.mockRejectedValue(new TypeError('Mac unavailable'));
  const saved = vi.fn();
  render(<ProposedVerdictCard itemKey="P1" proposal={{ proposed: 'dont_read', confidence: 0.9 }} onSaved={saved} />);
  fireEvent.click(screen.getByRole('button', { name: /Confirm/ }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(queueRejectTag).not.toHaveBeenCalled();
});

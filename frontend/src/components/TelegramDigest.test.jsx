// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import TelegramDigest from './TelegramDigest.jsx';
afterEach(cleanup);

it('separates broadcast lookahead from empty replies and displays retained not-attempted post coverage', () => {
  const batch = { target: { kind: 'broadcast', username: 'alpha' }, origin: { channel_id: -1000000000001, message_id: 8, text: 'Saved news' },
    status: 'empty', messages: [], attempted: true,
    post_window: { limit: 1, scanned: 2, retained: 1, lookahead: 1, truncated: true } };
  render(<TelegramDigest report={{ metadata: { schema_version: 2, source: 'telegram', status: 'partial', scope: 'Bounded context' },
    conversations: [batch, { ...batch, origin: { ...batch.origin, message_id: 9 }, status: 'unavailable', attempted: false, reason: 'not_attempted_after_flood_wait' }],
    topics: [], errors: [] }} />);
  expect(screen.getAllByText(/Post window: scanned 2; retained 1; lookahead 1; truncated/)).toHaveLength(2);
  expect(screen.getByText(/Replies\/messages: empty/)).toBeTruthy();
  expect(screen.getByText(/not attempted.*not_attempted_after_flood_wait/)).toBeTruthy();
});

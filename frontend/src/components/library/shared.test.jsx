// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FullTextAccessNotice } from './shared.jsx';
afterEach(cleanup);
function notice(deep) {
  return render(<MemoryRouter initialEntries={['/paper/SYNTHETIC?tab=review']}><FullTextAccessNotice deep={deep} /></MemoryRouter>);
}
it('shows setup recovery instead of a sign-in instruction when browser support is absent', () => {
  notice({ needs_pdf: true, acquire_outcome: 'browser_extra_unavailable', needs_login: true, login_url: 'https://publisher.example/login' });
  expect(screen.getByText(/unavailable in this installation/i)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Open access settings' }).getAttribute('href')).toBe('/settings?task=university');
  expect(screen.queryByText(/publisher page/i)).toBeNull(); expect(screen.queryByText(/no readable full text/i)).toBeNull();
});
it('keeps attempted access failure distinct from missing full text', () => {
  const first = notice({ needs_pdf: true, needs_login: true, login_url: 'https://publisher.example/login' });
  expect(screen.getByText(/session could not access/i)).toBeTruthy();
  expect(screen.getByRole('link', { name: /university access/i }).getAttribute('href')).toBe('/settings?task=university');
  expect(screen.queryByRole('link', { name: /publisher page/i })).toBeNull(); first.unmount();
  notice({ needs_pdf: true, needs_login: false }); expect(screen.getByText(/no readable full text/i)).toBeTruthy();
});

// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import Sheet from './Sheet.jsx';

afterEach(cleanup);

function Harness({ variant }) {
  const [open, setOpen] = useState(false);
  return <>
    <main><button onClick={() => setOpen(true)}>Open row</button><a href="#x">Behind</a></main>
    {open && <Sheet title="Paper detail" variant={variant} onClose={() => setOpen(false)}>
      <button>First action</button><textarea aria-label="Note" /><button>Last action</button>
    </Sheet>}
  </>;
}
const openSheet = (variant) => {
  render(<Harness variant={variant} />);
  const opener = screen.getByRole('button', { name: 'Open row' });
  opener.focus();
  fireEvent.click(opener);
  return opener;
};

it('is a modal dialog named by its title, with focus on the title', () => {
  openSheet();
  const dialog = screen.getByRole('dialog', { name: 'Paper detail' });
  expect(dialog.getAttribute('aria-modal')).toBe('true');
  expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Paper detail' }));
});

it('closes on Esc and returns focus to the opener', () => {
  const opener = openSheet();
  fireEvent.keyDown(document.activeElement, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
});

it('closes from the Close button and from the scrim', () => {
  const opener = openSheet();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
  fireEvent.click(opener);
  fireEvent.click(document.querySelector('[aria-hidden="true"]'));
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('keeps Tab and Shift+Tab inside the sheet', () => {
  openSheet();
  const close = screen.getByRole('button', { name: 'Close' });
  const last = screen.getByRole('button', { name: 'Last action' });
  last.focus();
  expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(false); // preventDefault → wrapped
  expect(document.activeElement).toBe(close);
  expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(false);
  expect(document.activeElement).toBe(last);
  screen.getByRole('heading').focus();
  fireEvent.keyDown(document.activeElement, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(last);
  screen.getByRole('link', { name: 'Behind', hidden: true }).focus();
  fireEvent.keyDown(document.body, { key: 'Tab' });
  expect(document.activeElement).toBe(close);
  expect(fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Tab' })).toBe(true); // middle: browser moves on
});

it('makes everything else inert and unscrollable while open, and restores both', () => {
  document.body.style.overflow = 'auto';
  openSheet();
  const main = document.querySelector('main').parentElement;
  expect(main.hasAttribute('inert')).toBe(true);
  expect(screen.getByRole('dialog').closest('[inert]')).toBeNull();
  expect(document.body.style.overflow).toBe('hidden');
  fireEvent.keyDown(document.activeElement, { key: 'Escape' });
  expect(main.hasAttribute('inert')).toBe(false);
  expect(document.body.style.overflow).toBe('auto');
});

it.each([
  ['full', 'inset-0'],
  ['side', 'w-[420px]'],
  ['bottom', 'max-h-[85%]'],
])('%s variant sizes the panel; all use 100dvh and the safe-area inset', (variant, cls) => {
  openSheet(variant);
  const dialog = screen.getByRole('dialog');
  expect(dialog.dataset.variant).toBe(variant);
  expect(dialog.className).toContain(cls);
  expect(dialog.className).toContain('pb-[env(safe-area-inset-bottom)]');
  expect(dialog.parentElement.className).toContain('h-dvh');
});

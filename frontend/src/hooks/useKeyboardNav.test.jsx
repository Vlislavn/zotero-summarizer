// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, renderHook } from '@testing-library/react';
import useKeyboardNav from './useKeyboardNav.js';

afterEach(cleanup);

it.each([
  <select><option>Algorithms</option></select>,
  <textarea />,
  <input />,
  <input type="checkbox" />,
  <button><span>Save</span></button>,
  <a href="#paper">Paper</a>,
  <details><summary>Options</summary></details>,
  <div contentEditable suppressContentEditableWarning><span>Notes</span></div>,
  <div contentEditable="plaintext-only"><span>Notes</span></div>,
  <div role="combobox" tabIndex={0}><span>Collection</span></div>,
  <div role="listbox" tabIndex={0}><span>Collection</span></div>,
  <div role="slider" tabIndex={0} />,
])('leaves interactive controls their own keys: %#', (control) => {
  const onNext = vi.fn(), onPrev = vi.fn(), onAction = vi.fn();
  renderHook(() => useKeyboardNav({ onNext, onPrev, onAction, actionKeys: { a: 'apply', Enter: 'commit', ' ': 'select' } }));
  const { container } = render(control);
  const target = container.querySelector('span, summary') || container.firstElementChild;

  for (const key of ['j', 'k', 'a', 'Enter', ' ']) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    fireEvent(target, event);
    expect(event.defaultPrevented).toBe(false);
  }

  expect(onNext).not.toHaveBeenCalled();
  expect(onPrev).not.toHaveBeenCalled();
  expect(onAction).not.toHaveBeenCalled();
});

it('keeps list shortcuts and removes its listener on unmount', () => {
  const onNext = vi.fn(), onPrev = vi.fn(), onAction = vi.fn();
  const { unmount } = renderHook(() => useKeyboardNav({
    onNext, onPrev, onAction, actionKeys: { a: 'apply' },
  }));

  for (const key of ['j', 'k', 'a']) fireEvent.keyDown(document.body, { key });

  expect(onNext).toHaveBeenCalledTimes(1);
  expect(onPrev).toHaveBeenCalledTimes(1);
  expect(onAction).toHaveBeenCalledWith('apply', 'a');
  unmount();
  fireEvent.keyDown(document.body, { key: 'a' });
  expect(onAction).toHaveBeenCalledTimes(1);
});

it.each(['claimed', 'composing', 'modifier'])('ignores %s events', (kind) => {
  const onAction = vi.fn();
  renderHook(() => useKeyboardNav({ onAction, actionKeys: { a: 'apply' } }));
  const event = new KeyboardEvent('keydown', {
    key: 'a', bubbles: true, cancelable: true, isComposing: kind === 'composing', ctrlKey: kind === 'modifier',
  });
  if (kind === 'claimed') event.preventDefault();

  fireEvent(document.body, event);

  expect(onAction).not.toHaveBeenCalled();
});

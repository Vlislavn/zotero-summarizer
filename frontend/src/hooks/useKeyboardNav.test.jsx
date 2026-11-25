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

it('lets controls matching `allowFrom` keep j / k / action keys, but leaves them Enter and other controls', () => {
  const onNext = vi.fn(), onAction = vi.fn(), onEnter = vi.fn();
  renderHook(() => useKeyboardNav({ onNext, onAction, onEnter, actionKeys: { 2: 'should' }, allowFrom: '.row button' }));
  const { getByText } = render(<><div className="row"><button>Row</button></div><button>Other</button></>);

  const press = (node, key) => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    fireEvent(node, event);
    return event.defaultPrevented;
  };
  expect([press(getByText('Row'), 'j'), press(getByText('Row'), '2')]).toEqual([true, true]);
  expect(press(getByText('Row'), 'Enter')).toBe(false);  // Enter stays the button's own: it opens the row
  expect([press(getByText('Other'), 'j'), press(getByText('Other'), '2')]).toEqual([false, false]);
  expect(onNext).toHaveBeenCalledTimes(1);
  expect(onAction).toHaveBeenCalledTimes(1);
  expect(onEnter).not.toHaveBeenCalled();
});

it('calls onEnter / onEscape / onUndo / onSlash only where a handler is given, and not for a held key', () => {
  const onNext = vi.fn(), onAction = vi.fn(), onEnter = vi.fn(), onEscape = vi.fn(), onUndo = vi.fn(), onSlash = vi.fn();
  renderHook(() => useKeyboardNav({ onNext, onAction, onEnter, onEscape, onUndo, onSlash, actionKeys: { 4: 'no' } }));
  const press = (init) => {
    const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
    fireEvent(document.body, event);
    return event.defaultPrevented;
  };

  expect(['Enter', 'Escape', 'z', '/'].map((key) => press({ key }))).toEqual([true, true, true, true]);
  expect([onEnter, onEscape, onUndo, onSlash].map((fn) => fn.mock.calls.length)).toEqual([1, 1, 1, 1]);

  expect(press({ key: '4' })).toBe(true);
  for (const key of ['Enter', 'Escape', 'z', '/', '4']) expect(press({ key, repeat: true })).toBe(false);
  expect(onAction).toHaveBeenCalledTimes(1);
  expect([onEnter, onEscape, onUndo, onSlash].map((fn) => fn.mock.calls.length)).toEqual([1, 1, 1, 1]);
  press({ key: 'j', repeat: true });  // holding j still scrolls
  expect(onNext).toHaveBeenCalledTimes(1);
});

it('an open modal dialog silences every key, unless it holds the allowModal content', () => {
  const onNext = vi.fn(), onAction = vi.fn(), onEnter = vi.fn();
  renderHook(() => useKeyboardNav({
    onNext, onAction, onEnter, actionKeys: { 1: 'must' }, allowModal: '[data-ours]',
  }));
  const press = () => ['j', '1', 'Enter'].forEach((key) => fireEvent.keyDown(document.body, { key }));
  const { container } = render(<div role="dialog" aria-modal="true" />);
  press();
  expect(onNext).not.toHaveBeenCalled();
  expect(onAction).not.toHaveBeenCalled();
  expect(onEnter).not.toHaveBeenCalled();

  container.firstElementChild.innerHTML = '<p data-ours></p>';
  press();
  expect(onNext).toHaveBeenCalledTimes(1);
  expect(onAction).toHaveBeenCalledTimes(1);
  expect(onEnter).toHaveBeenCalledTimes(1);
});

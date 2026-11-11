// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { CheckboxField, Field, SectionCard } from './Fields.jsx';
import palette from '../../../tailwind.config.js';
afterEach(cleanup);
// Reuse the WCAG calculation from PaperCard.selection.test.jsx, not the ITU luma proxy.
function luminance(hex) {
  const rgb = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const linear = rgb.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
it('keeps small form and section descriptions above WCAG normal-text contrast on existing surfaces', () => {
  render(<SectionCard title="Synthetic section" description="Section help">
    <Field label="Synthetic field" value="" onChange={vi.fn()} hint="Field help" />
    <CheckboxField label="Synthetic consent" checked={false} onChange={vi.fn()} hint="Consent help" />
  </SectionCard>);
  const colors = palette.theme.extend.colors;
  for (const text of ['Synthetic section', 'Section help', 'Field help', 'Consent help']) {
    const token = [...screen.getByText(text).classList].find(name => name.startsWith('text-slate-'));
    const foreground = colors.slate[token.split('-')[2]];
    for (const background of [colors.white, colors.slate[50]]) {
      const values = [luminance(foreground), luminance(background)];
      expect((Math.max(...values) + 0.05) / (Math.min(...values) + 0.05)).toBeGreaterThanOrEqual(4.5);
    }
  }
});
it.each([['text', 'textbox'], ['textarea', 'textbox'], ['number', 'spinbutton'], ['select', 'combobox']])(
  '%s keeps its visible label as the control name and associates help as description', (kind, role) => {
    render(<Field label="Synthetic field" kind={kind} value={kind === 'number' ? 1 : ''} options={['']} onChange={vi.fn()} hint="Separate help text" />);
    const control = screen.getByRole(role, { name: 'Synthetic field' });
    expect(control.getAttribute('aria-describedby')).toBe(screen.getByText('Separate help text').id);
    expect(screen.getByLabelText('Synthetic field')).toBe(control);
  });
it('associates the current error as a description without adding it to the field name', () => {
  render(<Field label="Synthetic field" value="" onChange={vi.fn()} error="Synthetic validation error" />);
  const control = screen.getByRole('textbox', { name: 'Synthetic field' });
  expect(control.getAttribute('aria-describedby')).toBe(screen.getByText('Synthetic validation error').id);
  expect(control.getAttribute('aria-invalid')).toBe('true');
});
it('honors the disabled state already supplied by source/consent owners', () => {
  render(<CheckboxField label="Synthetic consent" checked={false} disabled onChange={vi.fn()} />);
  expect(screen.getByRole('checkbox', { name: 'Synthetic consent' }).disabled).toBe(true);
});

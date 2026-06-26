import { describe, expect, it } from 'vitest';
import { humanizeError } from './humanizeError.js';

describe('humanizeError', () => {
  it('strips a leading "HTTP <status>:" prefix and keeps the detail', () => {
    expect(humanizeError(new Error('HTTP 400: bad request body'))).toBe(
      'bad request body',
    );
  });

  it('strips a "HTTP <status> <statusText>:" prefix too', () => {
    expect(
      humanizeError(new Error('HTTP 500 Internal Server Error: db locked')),
    ).toBe('db locked');
  });

  it('maps a bare 503 (no detail) to a friendly unavailable message', () => {
    const err = new Error('HTTP 503 Service Unavailable');
    expect(humanizeError(err)).toMatch(/unavailable/i);
  });

  it('uses err.status when present over parsing the message', () => {
    const err = new Error('HTTP 503');
    err.status = 503;
    expect(humanizeError(err)).toMatch(/unavailable/i);
  });

  it('keeps a backend detail message verbatim when there is no HTTP prefix', () => {
    expect(humanizeError(new Error('Zotero database was busy.'))).toBe(
      'Zotero database was busy.',
    );
  });

  it('accepts a plain string', () => {
    expect(humanizeError('something specific failed')).toBe(
      'something specific failed',
    );
  });

  it('accepts a {message} object', () => {
    expect(humanizeError({ message: 'object with message' })).toBe(
      'object with message',
    );
  });

  it('accepts a {detail} object (FastAPI shape)', () => {
    expect(humanizeError({ detail: 'detail field text' })).toBe(
      'detail field text',
    );
  });

  it('NEVER returns "[object Object]" for an opaque object', () => {
    const out = humanizeError({ weird: { nested: true } });
    expect(out).not.toBe('[object Object]');
    expect(out).not.toContain('[object Object]');
    expect(typeof out).toBe('string');
    expect(out.length).toBeGreaterThan(0);
  });

  it('returns a generic message for null / undefined', () => {
    expect(typeof humanizeError(null)).toBe('string');
    expect(humanizeError(null).length).toBeGreaterThan(0);
    expect(humanizeError(undefined)).not.toBe('[object Object]');
  });

  it('maps a numeric .status with empty message to its friendly line', () => {
    const err = { status: 404, message: 'HTTP 404' };
    expect(humanizeError(err)).toMatch(/found/i);
  });

  it('handles a status-only object with no usable message', () => {
    const out = humanizeError({ status: 500 });
    expect(out).toMatch(/internal error|try again/i);
    expect(out).not.toBe('[object Object]');
  });
});

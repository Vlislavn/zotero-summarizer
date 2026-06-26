// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Reachability is the live dot's only data source; mock it so the render is
// deterministic and offline.
vi.mock('../../api/settingsApi.js', () => ({
  fetchLlmReachability: vi.fn().mockResolvedValue({
    status: 'ok',
    stages: [{ stage: 'feed', reachable: true }],
  }),
}));

import ActiveModelsSummary from './ActiveModelsSummary.jsx';

const routing = {
  providers: [
    { name: 'local', type: 'openai', base_url: 'http://x/v1', api_key_env: 'K',
      temperature: 0.4, thinking_effort: 'low' },
    { name: 'claude', type: 'anthropic', api_key_env: 'A', thinking_effort: 'high' },
  ],
  default: { provider: 'local', model: 'base-model' },
  feed: { provider: null, model: null },          // inherits default
  backlog: { provider: null, model: null },
  deep_review: { provider: 'claude', model: 'opus' },
};

function renderWith(props) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ActiveModelsSummary routing={routing} {...props} />
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());
beforeEach(() => vi.clearAllMocks());

describe('ActiveModelsSummary', () => {
  it('renders resolved provider·model per stage with inheritance + knob badges', () => {
    renderWith();
    // feed AND backlog inherit the default provider+model and are marked as such.
    expect(screen.getAllByText('local · base-model').length).toBe(2);
    expect(screen.getAllByText('inherits default').length).toBe(2);
    // deep_review uses its override.
    expect(screen.getByText('claude · opus')).toBeTruthy();
    // openai temperature badge shown; anthropic is n/a; effort levels surfaced.
    expect(screen.getAllByText('temp 0.4').length).toBe(2); // feed + backlog (local)
    expect(screen.getByText('temp n/a')).toBeTruthy();      // deep_review (anthropic)
    expect(screen.getByText('high')).toBeTruthy();           // claude effort
  });

  it('clicking a stage row invokes onEdit (opens the editor)', () => {
    const onEdit = vi.fn();
    renderWith({ onEdit });
    fireEvent.click(screen.getByText('claude · opus').closest('button'));
    expect(onEdit).toHaveBeenCalled();
  });
});

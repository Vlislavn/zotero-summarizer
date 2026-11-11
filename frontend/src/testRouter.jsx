import './testSetup.js';
import { render } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

export function renderRouter(element, client, initialEntries = ['/settings']) {
  const router = createMemoryRouter([{ path: '*', element }], { initialEntries });
  return { ...render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>), router };
}

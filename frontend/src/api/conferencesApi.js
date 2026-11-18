import { request } from './client.js';

const base = '/api/conferences';
const edition = (id, year) => `${base}/${encodeURIComponent(id)}/${encodeURIComponent(year)}`;

// Reads are local files. Only `refresh` reaches the network, and only on the owner's click.
export const conferencesApi = {
  list: () => request(base),
  view: (id, year, { page, q }) => request(`${edition(id, year)}?page=${page}&q=${encodeURIComponent(q)}`),
  abstract: (id, year, doi) => request(`${edition(id, year)}/abstract?doi=${encodeURIComponent(doi)}`),
  preflight: (id, year) => request(`${edition(id, year)}/preflight`),
  refresh: (id, year) => request(`${edition(id, year)}/refresh`, { method: 'POST' }),
};

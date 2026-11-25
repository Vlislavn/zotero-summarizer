import { request } from './client.js';

const base = '/api/conferences';
const edition = (id, year) => `${base}/${encodeURIComponent(id)}/${encodeURIComponent(year)}`;

// Reads and `importFile` (the owner's CSV/XLSX, merged on this computer) are local. Only `refresh` reaches the network, and only on the owner's click.
export const conferencesApi = {
  list: () => request(base),
  view: (id, year, { page, q }) => request(`${edition(id, year)}?page=${page}&q=${encodeURIComponent(q)}`),
  abstract: (id, year, ref) => request(`${edition(id, year)}/abstract?id=${encodeURIComponent(ref)}`),
  preflight: (id, year) => request(`${edition(id, year)}/preflight`),
  refresh: (id, year) => request(`${edition(id, year)}/refresh`, { method: 'POST' }),
  importFile: (id, year, file) => request(`${edition(id, year)}/import?name=${encodeURIComponent(file.name)}`,
    { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }),
};

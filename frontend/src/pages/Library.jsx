import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchCollections,
  fetchTags,
  startTriage,
  fetchReadingQueue,
} from '../api/libraryApi.js';
import ReadNextView from '../components/library/ReadNextView.jsx';
import { StatusBanner } from '../components/library/shared.jsx';

// Library page — a single "Read next" surface (Stage 2). The former Browse tab
// and Triage monitor are merged in: the sidebar collection/tag filters + a
// search box scope the ranked queue, and an opt-in "Select" mode sends papers to
// triage. Scoring never runs on open; the Rescore button owns recompute.

const QUEUE_LIMIT = 50;

function flattenCollections(nodes, depth = 0) {
  const flat = [];
  for (const node of nodes || []) {
    flat.push({ key: node.key, name: node.name, item_count: node.item_count || 0, depth });
    if (node.children?.length) {
      flat.push(...flattenCollections(node.children, depth + 1));
    }
  }
  return flat;
}

export default function Library() {
  const navigate = useNavigate();
  const [queue, setQueue] = useState([]);
  const [queueMeta, setQueueMeta] = useState({
    read_hidden: 0, total_unread: 0, status: 'ready', model_ready: true,
    error: null, computed_at: null, scores_stale: false,
  });
  const pollRef = useRef(null);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueErr, setQueueErr] = useState(null);
  const [includeRead, setIncludeRead] = useState(false);
  const [collections, setCollections] = useState([]);
  const [tags, setTags] = useState([]);
  const [selectedCollection, setSelectedCollection] = useState('');
  const [selectedTag, setSelectedTag] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [starting, setStarting] = useState(false);
  const [message, setMessage] = useState('');
  const [isError, setIsError] = useState(false);

  const flatCollections = useMemo(() => flattenCollections(collections), [collections]);

  // ------ Initial sidebar load ------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cData, tData] = await Promise.all([fetchCollections(), fetchTags({ limit: 300 })]);
        if (cancelled) return;
        setCollections(cData?.items || []);
        setTags(tData?.items || []);
      } catch (err) {
        if (!cancelled) {
          setMessage(`Failed to load sidebar: ${err.message || err}`);
          setIsError(true);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ------ Read-next queue load (Stage 2) ------
  const loadQueue = useCallback(async (force = false) => {
    setQueueLoading(true);
    setQueueErr(null);
    try {
      const data = await fetchReadingQueue({
        includeRead, limit: QUEUE_LIMIT, refresh: force,
        collection: selectedCollection, tag: selectedTag, search,
      });
      setQueue(data?.items || []);
      setQueueMeta({
        read_hidden: data?.read_hidden || 0,
        total_unread: data?.total_unread || 0,
        status: data?.status || 'ready',
        model_ready: data?.model_ready !== false,
        error: data?.error || null,
        computed_at: data?.computed_at || null,
        scores_stale: Boolean(data?.scores_stale),
      });
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
      // Background scoring in progress → poll (without forcing another rescore).
      if (data?.status === 'computing') {
        pollRef.current = setTimeout(() => loadQueue(false), 4000);
      }
    } catch (err) {
      setQueueErr(err);
      setQueue([]);
    } finally {
      setQueueLoading(false);
    }
  }, [includeRead, selectedCollection, selectedTag, search]);

  useEffect(() => {
    loadQueue();
    return () => {
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    };
  }, [loadQueue]);

  function selectCollection(key) {
    setSelectedCollection(key);
    setSelected(new Set());
  }

  function selectTag(tag) {
    setSelectedTag(tag);
    setSelected(new Set());
  }

  function applySearch() {
    setSearch(searchInput.trim());
  }

  function clearFilters() {
    setSelectedCollection('');
    setSelectedTag('');
    setSearchInput('');
    setSearch('');
  }

  function toggleItem(key) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function handleRunTriage() {
    if (!selected.size) return;
    setStarting(true);
    setMessage('');
    try {
      const data = await startTriage([...selected], { queueChanges: true });
      setMessage(`Triage job ${data?.job_id || 'started'}. Opening Triage Monitor…`);
      setIsError(false);
      navigate('/triage');
    } catch (err) {
      setMessage(`Failed to start triage: ${err.message || err}`);
      setIsError(true);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      {/* ----- Sidebar: collections + top tags ----- */}
      <aside className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-1">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-2">Collections</h2>
        <div className="max-h-72 overflow-auto pr-1 slim-scroll">
          <button
            type="button"
            onClick={() => selectCollection('')}
            className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
              selectedCollection === ''
                ? 'bg-teal-600 text-white'
                : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
            }`}
          >
            All items
          </button>
          {flatCollections.map((entry) => (
            <button
              type="button"
              key={entry.key}
              onClick={() => selectCollection(entry.key)}
              style={{ paddingLeft: 8 + entry.depth * 14 }}
              className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
                selectedCollection === entry.key
                  ? 'bg-teal-600 text-white'
                  : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
              }`}
            >
              <span>{entry.name}</span>
              <span className="mono opacity-70"> ({entry.item_count})</span>
            </button>
          ))}
        </div>

        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mt-4 mb-2">Top Tags</h2>
        <div className="max-h-64 overflow-auto pr-1 slim-scroll">
          <button
            type="button"
            onClick={() => selectTag('')}
            className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
              selectedTag === ''
                ? 'bg-amber-600 text-white'
                : 'bg-amber-50 text-amber-800 hover:bg-amber-100'
            }`}
          >
            All tags
          </button>
          {tags.slice(0, 100).map((tagEntry) => (
            <button
              type="button"
              key={tagEntry.tag}
              onClick={() => selectTag(tagEntry.tag)}
              className={`w-full text-left text-xs px-2 py-1 rounded mb-1 ${
                selectedTag === tagEntry.tag
                  ? 'bg-amber-600 text-white'
                  : 'bg-amber-50 text-amber-800 hover:bg-amber-100'
              }`}
            >
              <span>{tagEntry.tag}</span>
              <span className="mono opacity-70"> ({tagEntry.item_count})</span>
            </button>
          ))}
        </div>
      </aside>

      {/* ----- Main: the single Read-next surface ----- */}
      <div className="glass rounded-2xl border border-slate-200 p-3 lg:col-span-3">
        <div className="flex flex-wrap gap-2 items-center mb-3">
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyUp={(e) => { if (e.key === 'Enter') applySearch(); }}
            placeholder="Search title, abstract, tags"
            className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
          />
          <button
            type="button"
            onClick={applySearch}
            className="px-3 py-2 rounded-lg bg-slate-900 text-white text-sm hover:bg-slate-700"
          >
            Search
          </button>
          {(selectedCollection || selectedTag || search) && (
            <button
              type="button"
              onClick={clearFilters}
              className="px-3 py-2 rounded-lg bg-slate-200 text-slate-700 text-sm hover:bg-slate-300"
            >
              Clear
            </button>
          )}
        </div>

        <StatusBanner message={message} isError={isError} />

        <ReadNextView
          items={queue}
          loading={queueLoading}
          err={queueErr}
          includeRead={includeRead}
          onToggleIncludeRead={() => setIncludeRead((v) => !v)}
          readHidden={queueMeta.read_hidden}
          totalUnread={queueMeta.total_unread}
          status={queueMeta.status}
          modelReady={queueMeta.model_ready}
          error={queueMeta.error}
          computedAt={queueMeta.computed_at}
          scoresStale={queueMeta.scores_stale}
          onRescore={() => loadQueue(true)}
          onSaved={() => loadQueue()}
          selectMode={selectMode}
          onToggleSelectMode={() => { setSelectMode((v) => !v); setSelected(new Set()); }}
          selected={selected}
          onToggleItem={toggleItem}
          onRunTriage={handleRunTriage}
          starting={starting}
        />
      </div>
    </div>
  );
}

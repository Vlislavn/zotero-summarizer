# Disconnected review

```text
Sync now -> device working set -> Library -> explicit verdict / manual note
                                     |                  |
                               compact context     durable UUID outbox
                                                        |
                             reconnect -> apply / conflict / reject
                                             |
                               compare -> explicit resolution -> pull
```

## Daily flow

1. At the same app address, **Sync now** while the Mac is available. The header
   reports a confirmed app sync, cached-paper count and last completed pull;
   browser connectivity by itself is not that acknowledgement.
2. Reopen **Library** while disconnected. The server-selected working set includes
   compact context for synced papers not previously opened. A full cached review
   remains usable when present. Local title/year sorting remains available; an
   unobserved collection/tag/meaning scope is not invented from the full set.
3. Expand a paper or **Open full review**. Explicitly save verdicts, comments,
   retractions and **My notes**. **Saved on this device** means a completed local
   transaction, not a canonical server change or Zotero delivery. Saved mutations
   survive a browser restart; unsaved text is still a draft requiring Save.
4. When the Mac returns, **Sync now** or refocus/reconnect this same client.
   The existing coordinator sends ordered typed UUID mutations, never databases.
   Replays retain the same identities and payloads. A rejected draft remains
   available to copy/correct; it does not silently become an applied action.
5. For **Changed elsewhere**, compare the server and device values/comments.
   **Keep mine** and **Use server** each create one new auditable resolution.
   Another field may merge without a conflict. **Use server** sends a bounded
   `keep_server` reference to the compared conflict/revision; it does not echo or
   truncate long server content. A changed server version conflicts again, with
   the original device intent retained locally for comparison/recovery. Its
   successful no-op receipt changes no field/history/native mirror and confirms
   no past native delivery. A missing/invalid conflict or failed local resolution
   transaction does not discard either version. **Keep mine** immediately updates
   the durable device paper and detail, even if the Mac is unavailable.

## What is and is not retained

| On this device | Still needs the Mac |
|---|---|
| Synced cards and available compact review context | New AI review or Ask Paper execution |
| Explicit verdict/comment changes and retractions | PDF acquisition, figures and annotations |
| Explicitly saved manual notes | Native collection/tag edits and Zotero export |
| Pending, conflicted and rejected mutations | Inference, rescoring and new meaning search |

Compact snapshots do not claim a current tag/collection membership or a local PDF.
Those editors remain unavailable until a full detail and app connection are available.
Existing Zotero PDF/annotation/citation workflows remain usable separately.

## Failure and persistence boundaries

- Do not clear site data or uninstall the browser profile with unsynced work.
  Browser storage can be denied, quota-limited or evicted; a storage error is not
  a confirmed device save. Keep unsaved text until storage works. If pending local
  fields cannot be read, a server detail refresh also refuses rather than hiding
  a durable device edit behind stale server text.
- Refresh must not overwrite an edited note/verdict. Late completions for a
  previous paper must not update the current editor or collapse its review.
  Prev/Next and inline-close warnings protect dirty manual notes. Other navigation
  or browser closure can discard unsaved drafts; only explicit successful saves
  are promised to survive browser restart.
- An unknown/failed pending-count read is displayed as unavailable, not zero.
  A protocol/HTTP/storage block is distinct from a transport outage. Received
  HTTP refusals and malformed acknowledgements do not mint offline-success
  mutations. After a reported transport outage, local capture need not repeat a
  blocking network attempt; it still cannot claim server acknowledgement. If the
  server committed an online write but its response was lost, reconciliation may
  conservatively ask you to compare identical text. **Use server** records a no-op
  receipt; the unconfirmed response is not proof of native delivery.
- Full pulls validate their identities/cursor and commit context plus cursor
  together. Pending local fields overlay incoming context. A malformed/stale pull
  or invalid/foreign acknowledgement preserves device state rather than advancing
  the cursor or retiring unrelated work.

Offline-capable requests and the existing sync round use the same named safety
budget. Build-time `VITE_SYNC_TIMEOUT_MS` defaults to15000ms and must be finite,
positive and at most60000ms. Invalid configuration fails before a network effect.
Ordinary slower APIs keep their existing policy. This cap is not a performance
measurement or a substitute for successful sync. It cancels network work, not
browser storage operations; storage availability and transaction completion remain
separate requirements.

## Installation and acceptance

The header's **Offline use & installation** disclosure reports whether a service
worker currently controls the page. Test a reload with the server stopped before
relying on that browser. An install icon/controller alone does not establish that
all desired content is cached. Use Safari **Share → Add to Home Screen** or the
browser install action where supported.

Loopback remains the default local-server boundary. Another device needs a
separately supported, explicitly authenticated HTTPS gateway and a trusted origin;
never expose `/api/sync` on an arbitrary LAN port or bypass TLS/origin warnings.

Fake-indexeddb contracts, an owned persistent Chromium profile using real local
TCP/API/SQLite, physical iPhone/iPad use and remote gateway/revocation are separate
qualification scopes. Deterministic fixtures are not human/scientific acceptance,
real-user Zotero-write evidence, storage-lifetime guarantees or weeks of reading.
This client adds neither a native/Swift frontend nor PDF sync.

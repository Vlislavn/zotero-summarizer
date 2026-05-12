# Documentation

This folder contains the detailed docs for Zotero Summarizer.

- [How It Works](architecture.md): package structure, runtime lifecycle, triage pipeline, storage, and Zotero write safety.
- [Configuration](configuration.md): `.env`, `goals.yaml`, LLM config schema, and local storage paths.
- [API Schemas](api.md): canonical routes, request/response shapes, and error schema.
- [Operations](operations.md): setup, smoke tests, verification checklist, MCP usage, and troubleshooting notes.
- [RSS Feed Processor](feeds.md): **start here for the daemon workflow** — `feeds serve`, daily selection (target: 1–2 papers/day), outcome feedback loop, note v3 format, configuration reference.

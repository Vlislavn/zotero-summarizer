# Zotero Summarizer

A local-first assistant for reading research papers with Zotero. It collects papers
from RSS feeds, ranks them against your research goals, writes source-grounded reviews
you can question, and learns relevance from your own labels. Zotero stays your
citation/PDF library; the app keeps triage state and the history of your decisions.

```text
RSS → relevance gate → optional AI review → Today shortlist → you read / label / Add to Zotero
          ↑                                                         │
          └───────────────────── retrain on your labels ─────────────┘
```

## Quick start

You need macOS or Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/)
(Python 3.10+) and Node 20.19+. Zotero desktop is needed for library and PDF work.
An AI model is optional: a hosted API or a local model server.

```bash
make install   # once, and again after dependency changes
make run       # builds the UI when needed, then serves http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000>. The first launch creates `goals.yaml` and `.env` and
opens **Setup**: choose AI or ML-only, connect Zotero and describe your research goals.
Then add feeds in **Today → RSS sources** and run **Today → Triage backlog**.
Ctrl+C in the terminal stops the app.

## Everyday commands

| Command | Use it to |
|---|---|
| `make run` | use the app on this Mac at `http://127.0.0.1:8000` |
| `make iphone` | use the app on your iPhone (and this Mac) through Tailscale, see below |
| `make dev` | change the code: backend plus hot-reload UI at `http://localhost:5173` |
| `make help` | list every command, including checks |

Run one of `run`, `iphone` and `dev` at a time: they share port 8000 and your data.

## Use on iPhone

`make iphone` makes the app reachable over HTTPS from your own devices in your
[Tailscale](https://tailscale.com/download) network, and only for your Tailscale login.

One-time setup:

1. Install Tailscale on this Mac and on the iPhone and sign in to the same account.
2. In the [Tailscale admin console → DNS](https://login.tailscale.com/admin/dns), keep
   MagicDNS on and enable **HTTPS Certificates**.
3. Check that `tailscale status` works in Terminal; if not, install the command-line
   tool from the Tailscale app's settings.

Each time:

```bash
make iphone
```

It prints the address, such as `https://<mac>.<tailnet>.ts.net`. Open it in Safari on
the iPhone with Tailscale connected, then **Share → Add to Home Screen** for an app icon.
Use the same address on the Mac; `http://127.0.0.1:8000` answers 403 in this mode.
Ctrl+C stops the app and removes the address from your tailnet.
Details and limits: [remote access](docs/features.md#remote-access).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make` prints a list of commands | That is the help; start the app with `make run`. |
| `command not found` or `Frontend dependencies missing` | Run `make install`. |
| `address already in use` | Another `make run`, `dev` or `iphone` is running; stop it with Ctrl+C. |
| 403 `local_access_only` | `make run` answers only `http://127.0.0.1:8000` on this Mac; use `make iphone` for other devices. |
| 403 `Authenticated owner access …` | Open the exact `https://…ts.net` address that `make iphone` printed, signed in to Tailscale as the printed login. |
| iPhone cannot reach the address | Turn Tailscale on in the iPhone and check that `make iphone` is still running. |
| `make iphone`: Tailscale not connected / enable HTTPS | Follow the message; see one-time setup above. |
| Test failures or crashes | See [tests](tests/README.md#troubleshooting). |

## Settings, data and privacy

- Setup and **Settings** edit `goals.yaml` (goals, model routes) and `.env` (paths) in
  the settings folder: `ZOTERO_SUMMARIZER_HOME` if that variable is set, otherwise this
  checkout. `make run` and `make iphone` print the folder they use; app data lives in
  its `data/`. In a checkout these files are gitignored; do not commit them. API keys
  go to the OS keyring or a named environment variable.
- Local-first is not fully offline: hosted models and public-source lookups receive
  the inputs they need. Sending private comments to a model needs separate consent.
- The app never adds papers or writes notes and tags to Zotero without your explicit
  action, and backs up Zotero before writing.
- Optional Telegram sources: `uv sync --inexact --extra telegram`, then once
  `uv run zotero-summarizer research-feed telegram-auth` in Terminal, then
  **Weekly → Telegram sources**.

## Find your workflow

| You want to… | Start here |
|---|---|
| Cull feed papers | [Daily triage](docs/features.md#daily-triage) |
| Read a paper and ask questions | [Reading reviews](docs/features.md#reading-reviews), [questions and notes](docs/features.md#paper-questions-and-notes) |
| Find papers on a topic | [Search](docs/features.md#search) |
| Find related or new papers from a saved paper | [Related saved papers](docs/features.md#related-saved-papers), [discovery](docs/features.md#saved-paper-discovery) |
| Label papers and improve relevance | [Labels and learning](docs/features.md#labels-and-learning) |
| Browse saved weekly reports | [Weekly digests](docs/features.md#weekly-digests), [Telegram news](docs/features.md#telegram-news) |
| Work offline or from another device | [Offline review](docs/features.md#offline-review), [remote access](docs/features.md#remote-access) |
| Understand Zotero writes and recovery | [Zotero changes](docs/features.md#zotero-changes), [read-state backups](docs/features.md#rss-read-state-backups) |
| Configure AI models | [Setup](docs/features.md#setup-and-configuration), [model routing](docs/features.md#model-routing) |

## Documentation

```text
docs/features.md      what each feature does and its limits
docs/architecture.md  how the system is built and where data lives
docs/overrides.md     environment-variable reference (generated)
CONTRIBUTING.md       development setup, checks and documentation rules
CHANGELOG.md          change history
```

`uv run zotero-summarizer --help` lists the command-line tools; each command's
`--help` shows its options.

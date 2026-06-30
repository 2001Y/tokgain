# tokgain

[日本語版 README](README.ja.md)

**ccusage for token savings.** `tokgain` is a small local CLI for measuring and aggregating token savings from coding-agent compression tools such as RTK, Headroom, lean-ctx, h5i, and FFF.

It is intentionally file-first:

- one append-only source of truth: `~/.local/state/tokgain/events.jsonl`
- no Prometheus, Grafana, database, or SaaS required
- readable with `jq`, `tail`, `less`, and normal shell tools
- usable from Codex, Claude Code, Hermes, or any other agent runtime

## Quick start

Run directly with `uvx` from GitHub:

```bash
uvx --from git+https://github.com/2001Y/tokgain tokgain --help
```

Or install for repeated local use:

```bash
uv tool install git+https://github.com/2001Y/tokgain
tokgain --help
```

For local development:

```bash
git clone https://github.com/2001Y/tokgain.git
cd tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/tokgain --help
```

## One-minute demo

Record one event by comparing raw output with optimized output:

```bash
tokgain bench --tool demo --model gpt-5.5 \
  --baseline-cmd 'python3 - <<"PY"
for i in range(200):
    print(f"trace line {i}: repeated verbose tool output")
PY' \
  --optimized-cmd 'printf "summary: 200 repeated trace lines\n"'
```

View the result:

```bash
tokgain report --period day
tokgain report --period month
tail -n 5 ~/.local/state/tokgain/events.jsonl
```

Example output:

```text
2026-06-27 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
2026-06 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
```

That is the core workflow: measure or import savings, append JSONL events, then report later.

## What to use it for

`tokgain` helps answer questions like:

- How many prompt-equivalent tokens did a compression tool save?
- Which tool saved tokens in real agent workflows?
- Did a change reduce output size or just move the cost elsewhere?
- How much estimated API cost did the saved tokens represent?
- Which events were incomplete because model or price data was missing?

It does **not** perform compression itself. It records and normalizes savings reported or observed from other tools.

## Core commands

```bash
tokgain collect --tool auto
tokgain bench --tool TOOL --baseline-cmd CMD --optimized-cmd CMD
tokgain measure h5i --cmd CMD
tokgain measure fff --path PATH --query QUERY
tokgain observe terminal --agent AGENT --command CMD < output.txt
tokgain observe mcp-call --agent AGENT --server-tool TOOL --base-path PATH < payload.json
tokgain mcp-proxy --agent codex --tool fff --base-path PATH -- fff-mcp PATH
tokgain report --period day|week|month
tokgain show --limit 20
tokgain export --format jsonl|json
tokgain doctor
tokgain prices show
```

## Data model

Default state directory:

```text
~/.local/state/tokgain/
  events.jsonl
  daily/YYYY-MM-DD.json
  state.json
```

`events.jsonl` is the source of truth. `daily/*.json` and reports are derived artifacts.

Each event includes a stable `event_id`. Appending skips existing `event_id`s so re-importing the same native source record does not double count it. Semantic overlap between different tools or layers is not deduplicated in the first version.

Natural-use observations can also carry call-side context: `observed_call_count`, `duration_ms`, `turn_id`, `tool_call_id`, and `api_request_id`. Reports aggregate these as observed calls and unique IDs so token savings can be read together with “how many calls did this cost?”.

Typical fields:

```json
{
  "ts": "2026-06-28T12:00:00+09:00",
  "tool": "fff",
  "layer": "mcp-observer",
  "agent": "codex",
  "model": "gpt-5.5",
  "period": "2026-06-28",
  "saved_tokens": 1200,
  "saved_input_tokens": 1200,
  "saved_output_tokens": null,
  "estimate_mode": "prompt_equivalent",
  "usd_saved_estimate": 0.0,
  "price_table_version": "2026-06-28",
  "source_ref": "mcp:fff",
  "session_id": "..."
}
```

## Configuration and advanced usage

Most options are optional. Keep quick-start commands short and move environment-specific details here.

### Model resolution

`model` is resolved in this order:

1. explicit `--model`
2. metadata file from `--metadata` or `TOKGAIN_SESSION_METADATA`
3. adapter/native payload model
4. `TOKGAIN_MODEL`, `CODEX_MODEL`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`
5. `model_missing`

Events with `model_missing` are marked incomplete and excluded from totals.

### Price table

By default, `tokgain` tries to fetch live pricing in a ccusage-like order:

1. explicit `--prices` / `TOKGAIN_PRICES`
2. LiteLLM model prices
3. models.dev fallback
4. `~/.cache/tokgain/prices.json`
5. packaged placeholder fallback

Useful commands:

```bash
tokgain prices refresh
tokgain --offline-prices prices show
tokgain --prices ~/.config/tokgain/prices.json collect --tool auto --model gpt-5.5
```

### Adapter sources

| tool | source |
|---|---|
| RTK | `rtk gain --all --format json` |
| Headroom | `TOKGAIN_HEADROOM_FILE`, `~/.headroom/proxy_savings.json`, `headroom perf --format json` |
| lean-ctx | `TOKGAIN_LEAN_CTX_FILE`, `~/.lean-ctx/savings.jsonl`, `lean-ctx gain --json` |
| h5i | external JSONL from `h5i capture run --format json`-style summaries |
| FFF | external benchmark JSONL or observed MCP calls |

### Codex FFF MCP proxy

Use the single supported command shape: `tokgain mcp-proxy`.

```toml
[mcp_servers.fff]
command = "/Users/akitani/.local/bin/tokgain"
args = ["mcp-proxy", "--agent", "codex", "--tool", "fff", "--base-path", "/Users/akitani/_dev", "--", "/opt/homebrew/bin/fff-mcp", "/Users/akitani/_dev"]
```

No `tokgain-mcpproxy` executable or `mcpproxy` alias is provided. Keeping one command shape avoids configuration drift.

### Hermes hook observation

Hermes can record natural-use events through a plugin hook that calls `tokgain observe ...` after tool calls.

Example targets:

- terminal output from `h5i ...`
- terminal output from `ast-grep ...` / `sg ...`
- FFF MCP tool results

The observed events are written to the same `events.jsonl` ledger.

### h5i / FFF measurement

`h5i` and `fff-mcp` do not always expose a daily native savings ledger, so `tokgain measure` can generate baseline-vs-optimized events.

```bash
tokgain measure h5i --cmd 'pytest -q'
tokgain measure fff --path . --query 'PrepareUpload'
```

`measure h5i` runs the command twice: raw and via `h5i capture run`. Use it only for repeatable commands such as tests, builds, searches, and log inspection.

### Monthly report example

`tokgain report --period month` prints a compact line that can be pasted into Slack, cron, or a release note:

```text
2026-06 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
```

For a structured downstream workflow:

```bash
tokgain report --period month --json
```

### PyPI publishing status

The package metadata and GitHub Actions publish workflow are PyPI-ready. Until the first PyPI release is published, use the GitHub `uvx` form:

```bash
uvx --from git+https://github.com/2001Y/tokgain tokgain --help
```

After the first PyPI release, the shorter form will work:

```bash
uvx tokgain --help
uv tool install tokgain
```

## Development

```bash
git clone https://github.com/2001Y/tokgain.git
cd tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m tokgain.cli --help
```

Build and packaging checks:

```bash
uv build
uvx twine check dist/*
uvx --from . tokgain --help
```

## Project stance

- append-only local ledger first
- observable failures instead of silent success
- real tools over lookalike reimplementations
- no raw prompt/tool output stored by default
- one obvious command path per integration

## License

MIT

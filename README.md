# tokgain

`tokgain` は、RTK / headroom / lean-ctx / h5i / fff などの token 節約量を、ローカルの JSONL に追記して後から集計する小さなCLIです。

名前は `token` + `gain`。Codex専用名にせず、Claude Code などにも展開しやすい短い名前にしました。

## Install

```bash
cd /Users/akitani/_dev/tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e .
ln -sf /Users/akitani/_dev/tokgain/.venv/bin/tokgain ~/.local/bin/tokgain
~/.local/bin/tokgain --help
```

必要なら PATH に追加します。

```bash
export PATH="/Users/akitani/_dev/tokgain/.venv/bin:$PATH"
```

## Quick start

```bash
tokgain collect --tool all --model gpt-5.5 --allow-errors
tokgain bench --tool rg --layer search-output --model gpt-5.5 \
  --baseline-cmd 'rg "TODO|FIXME" .' \
  --optimized-cmd 'rg "TODO|FIXME" . --glob "!node_modules" | head -80'
tokgain measure h5i --cmd 'pytest -q' --model gpt-5.5
tokgain measure fff --path . --query 'TODO' --max-results 20 --model gpt-5.5
# Hermes hook / Codex MCP proxy からの自然利用計測も同じ events.jsonl に入る
tokgain report --period day
tokgain show --tool rtk --limit 20
```

正本は1つだけです。

```bash
tail -n 20 ~/.local/state/tokgain/events.jsonl
jq . ~/.local/state/tokgain/daily/$(date -v-1d +%F).json
```

## Commands

```bash
tokgain collect [--tool auto|all|rtk|headroom|lean-ctx|h5i|fff] [--date YYYY-MM-DD] [--model MODEL] [--allow-errors]
tokgain bench --tool TOOL (--baseline-file PATH|--baseline-cmd CMD) (--optimized-file PATH|--optimized-cmd CMD) [--layer LAYER] [--model MODEL]
tokgain measure h5i --cmd CMD [--h5i-format compact|json|summary] [--kind KIND] [--model MODEL]
tokgain measure fff --path PATH --query QUERY [--fff-tool grep|find_files] [--baseline-cmd CMD] [--model MODEL]
tokgain observe terminal --agent hermes --command CMD [--duration-ms MS] [--turn-id ID] [--tool-call-id ID] [--api-request-id ID] [--cwd DIR] [--model MODEL] < output.txt
tokgain observe mcp-call --agent hermes --server-tool fff --base-path PATH [--duration-ms MS] [--turn-id ID] [--tool-call-id ID] [--api-request-id ID] [--model MODEL] < payload.json
tokgain mcp-proxy --agent codex --tool fff --base-path PATH -- fff-mcp PATH
tokgain report --period day|week [--date YYYY-MM-DD] [--json]
tokgain show [--tool TOOL] [--status ok|error] [--limit N]
tokgain export --format jsonl|json
tokgain doctor [--json]
tokgain prices show
tokgain prices refresh
```

## Data layout

既定の保存先:

```text
~/.local/state/tokgain/
  events.jsonl
  daily/YYYY-MM-DD.json
  state.json
```

`events.jsonl` は追記専用の正本、`daily/*.json` は派生物です。

各 event には安定 `event_id` が付き、同じ `event_id` は再追記されません。これは同じ native ledger/source record を `collect` し直した時の二重計上を防ぐためです。RTK と Headroom など、異なる tool/layer 間の意味的重複は初版では排除しません。

## model の扱い

解決順:

1. `collect --model`
2. `collect --metadata` / `TOKGAIN_SESSION_METADATA`
3. adapter payload 内の `model`
4. `TOKGAIN_MODEL`, `CODEX_MODEL`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`
5. `model_missing`

`model_missing` は `incomplete=true`, `exclude_from_totals=true` になり、totals から外します。

## 価格表

価格表は ccusage と同じ考え方で取得します。

1. `--prices` / `TOKGAIN_PRICES` があれば、その手動JSONを使う。
2. 指定がなければ LiteLLM の `model_prices_and_context_window.json` を取得する。
3. LiteLLM に無いモデルは `models.dev/api.json` で補う。
4. 取得成功時は `~/.cache/tokgain/prices.json` に保存する。
5. `--offline-prices` 時やネットワーク失敗時は cache、最後に packaged placeholder を使う。

明示的に更新する場合:

```bash
tokgain prices refresh
```

手動JSONを固定したい場合:

```bash
tokgain --prices ~/.config/tokgain/prices.json collect --tool auto --model gpt-5.5
```

形式:

```json
{
  "version": "2026-06-22",
  "currency": "USD",
  "models": {
    "example-model": {
      "input_per_1m": 1.0,
      "output_per_1m": 2.0
    }
  }
}
```

未知モデルは `price_missing=true`、`usd_saved_estimate=0.0` としてイベント自体は残します。

## Adapter sources

| tool | source |
|---|---|
| rtk | `rtk gain --all --format json` |
| headroom | `TOKGAIN_HEADROOM_FILE`, `~/.headroom/proxy_savings.json`, `headroom perf --format json` |
| lean-ctx | `TOKGAIN_LEAN_CTX_FILE`, `~/.lean-ctx/savings.jsonl`, `lean-ctx gain --json` |
| h5i | `TOKGAIN_H5I_SUMMARY_FILE`, `~/.h5i/savings.jsonl` など `h5i capture run --format json` 由来の外部JSONL |
| fff | `TOKGAIN_FFF_FILE`, `~/.fff/savings.jsonl` などの外部ベンチ/エクスポートJSONL |

### Headroom integration policy

Headroom 内で token 節約ツールを統合する場合も、`tokgain` は「似た実装」を作りません。Headroom 側は本物のツールを呼びます。

- `rtk`: 実体の `rtk` binary / hooks / `rtk gain ...`
- `lean-ctx`: 実体の `lean-ctx` binary / setup / `lean-ctx gain --json`
- `h5i`: 実体の `h5i capture run` が保存した raw object / summary / ledger

Headroom は provider proxy と実体ツール orchestration、`tokgain` は Headroom/各ツールが出した savings event の append-only 集計に寄せます。

## Built-in measurement for h5i / fff

`h5i` と `fff-mcp` は日次の native ledger を持たないため、`collect` だけでは節約量を作れません。代わりに `tokgain measure` がツール側で baseline と optimized を実行/取得して、同じ `events.jsonl` に保存します。

```bash
# raw command と h5i capture run の出力を比較する。コマンドは2回走る。
tokgain measure h5i --cmd 'pytest -q' --kind test --model gpt-5.5

# rg の生出力と fff-mcp grep のMCP結果を比較する。
tokgain measure fff --path . --query 'PrepareUpload' --max-results 20 --model gpt-5.5

# fff の baseline を自分で固定したい場合
tokgain measure fff --path . --query 'PrepareUpload' \
  --baseline-cmd 'rg --line-number --column --no-heading --color never PrepareUpload src | head -20'
```

- `measure h5i` は指定コマンドを raw と `h5i capture run` で2回実行します。h5iの性質上、実行ディレクトリはgit repo内にしてください。副作用のあるコマンドには使わず、test/build/search/log確認など再実行可能なものに限定してください。
- `measure fff` は内部で `fff-mcp` にMCP接続し、`grep` または `find_files` を呼びます。比較対象の raw baseline は既定で `rg` / `find` ですが、厳密に揃えたい場合は `--baseline-cmd` を渡してください。
- `saved_tokens` は負数も許容します。小さい出力やmetadata過多では、h5i/fff側が増えることもあります。

## Benchmark mode

`rg`, `ast-grep`, `fff-mcp`, `tokensaver`, `contextmode`, `codereviewgraph`, Claude Code / Codex の独自context圧縮など、native ledger が無いものは `tokgain bench` で測ります。正本は同じ `events.jsonl` です。

```bash
# ファイル同士を比較
tokgain bench --tool ast-grep --layer search-output --model gpt-5.5 \
  --baseline-file /tmp/raw-rg.txt \
  --optimized-file /tmp/ast-grep-outline.txt

# コマンド出力同士を比較。stdout+stderrを、同じtokenizerで数える
tokgain bench --tool rg --layer search-output --model gpt-5.5 \
  --baseline-cmd 'rg "TODO|FIXME" .' \
  --optimized-cmd 'rg "TODO|FIXME" . --glob "!node_modules" | head -80'
```

- `saved_tokens = baseline_tokens - optimized_tokens`。悪化した場合は負数のまま残します。
- `saved_input_tokens` に同じ値を入れ、API換算は `prompt_equivalent` として扱います。
- 既定 tokenizer は `auto`。`tiktoken` があれば `o200k_base`、無ければ依存なしの `regex_v1` 概算です。再現性重視なら `--tokenizer regex` を明示します。

`fff` の公式実体は `fff-mcp` MCP server です。ファイル検索を高速・省コンテキスト化しますが、現時点では native な savings ledger を出さないため、`tokgain` は存在しない `fff stats` のようなコマンドを推測実行しません。fff の節約量は `tokgain measure fff` でMCP結果を直接測るか、外部ベンチ結果を `TOKGAIN_FFF_FILE` / `~/.fff/savings.jsonl` に1行1イベントで置いて集計します。

`collect --tool auto` は利用可能な source だけ読みます。`collect --tool all --allow-errors` は全adapterを毎回走らせ、native ledger が無い/対象日レコードが無い tool も ERROR event として残しつつ終了コードは0にします。明示指定した tool が失敗し、`--allow-errors` を付けない場合は ERROR event を残して終了コード `1` です。

## Natural-use instrumentation

普段の Codex / Hermes 利用中に自動で記録する場合も、正本は同じ `~/.local/state/tokgain/events.jsonl` です。

### Hermes

Hermes は plugin hook を使います。`~/.hermes/plugins/tokgain-observer/` の `post_tool_call` hook が fail-open で `tokgain observe ...` を呼びます。

対象:

- `terminal` tool 経由の `h5i ...`
- `terminal` tool 経由の `ast-grep ...` / `sg ...`
- `mcp_fff_*` など fff MCP tool 経由の検索

有効化:

```bash
hermes plugins enable tokgain-observer
hermes gateway restart   # gateway 利用時。CLI は新セッションで反映
```

### Codex

Codex は fff MCP server を `tokgain mcp-proxy` 経由にします。

```toml
[mcp_servers.fff]
command = "/Users/akitani/.local/bin/tokgain"
args = ["mcp-proxy", "--agent", "codex", "--tool", "fff", "--base-path", "/Users/akitani/_dev", "--", "/opt/homebrew/bin/fff-mcp", "/Users/akitani/_dev"]
```

Codex / Hermes 以外へ展開しても `capture_mode` と `agent` で区別できます。イベントには raw output 本文ではなく、tokens / bytes / sha256 / redacted metadata を保存します。

Hermes hook などから渡せる場合は `duration_ms`, `turn_id`, `tool_call_id`, `api_request_id` も保存します。これにより、token 節約量だけでなく wall time・retry・turn/API request 単位の相関を後から見られます。

## launchd

テンプレートだけ同梱しています。登録する場合:

```bash
cd /Users/akitani/_dev/tokgain
scripts/install-launchd.sh
```

毎日 0:00 に `tokgain collect --tool all --allow-errors` を実行します。launchd の最小PATHには `~/.local/bin` や Homebrew が入らないため、テンプレート側で `~/.local/bin:/opt/homebrew/bin:/usr/local/bin` を明示します。

## Development

```bash
cd /Users/akitani/_dev/tokgain
pytest -q
python -m tokgain.cli --help
```

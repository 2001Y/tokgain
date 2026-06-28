# tokgain

[English README](README.md)

**token savings 版 ccusage.** `tokgain` は、RTK / Headroom / lean-ctx / h5i / FFF などの token 節約量を、ローカルの JSONL に追記して後から集計する小さな CLI です。

方針:

- 正本は1つ: `~/.local/state/tokgain/events.jsonl`
- Prometheus / Grafana / DB / SaaS 不要
- `jq`, `tail`, `less` で読める
- Codex / Claude Code / Hermes など複数 agent runtime で使える

## Quick start

GitHub から `uvx` で直接実行:

```bash
uvx --from git+https://github.com/2001Y/tokgain tokgain --help
```

繰り返し使う場合:

```bash
uv tool install git+https://github.com/2001Y/tokgain
tokgain --help
```

ローカル開発:

```bash
git clone https://github.com/2001Y/tokgain.git
cd tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/tokgain --help
```

## 1分デモ

raw output と optimized output を比較して1件記録します。

```bash
tokgain bench --tool demo --model gpt-5.5 \
  --baseline-cmd 'python3 - <<"PY"
for i in range(200):
    print(f"trace line {i}: repeated verbose tool output")
PY' \
  --optimized-cmd 'printf "summary: 200 repeated trace lines\n"'
```

結果確認:

```bash
tokgain report --period day
tokgain report --period month
tail -n 5 ~/.local/state/tokgain/events.jsonl
```

出力例:

```text
2026-06-27 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
2026-06 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
```

基本は「計測/取り込み → JSONL追記 → 後からreport」です。

## 何に使うか

- 圧縮/検索/ログ要約ツールがどれだけ token を減らしたか確認する
- Codex / Hermes / Claude Code などの実利用で、どの tool が効いたか見る
- API 換算の節約額を概算する
- `model_missing` / `price_missing` など不完全な event を分ける

`tokgain` 自体は圧縮処理をしません。他ツールの自己申告や観測結果を正規化して保存します。

## 主なコマンド

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

## データ構造

```text
~/.local/state/tokgain/
  events.jsonl
  daily/YYYY-MM-DD.json
  state.json
```

`events.jsonl` が追記専用の正本です。`daily/*.json` と report は派生物です。

各 event には安定した `event_id` が付きます。同じ native source record を再取り込みしても二重計上しません。異なる tool/layer 間の意味的重複は初版では排除しません。

## 設定・応用

Quick start には最小引数だけ載せ、環境依存の設定はここに分けます。

### model 解決順

1. `--model`
2. `--metadata` / `TOKGAIN_SESSION_METADATA`
3. adapter payload 内の `model`
4. `TOKGAIN_MODEL`, `CODEX_MODEL`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`
5. `model_missing`

`model_missing` は incomplete として totals から外します。

### 価格表

既定では ccusage に近い考え方で価格を解決します。

1. `--prices` / `TOKGAIN_PRICES`
2. LiteLLM price table
3. models.dev fallback
4. `~/.cache/tokgain/prices.json`
5. packaged placeholder

```bash
tokgain prices refresh
tokgain --offline-prices prices show
tokgain --prices ~/.config/tokgain/prices.json collect --tool auto --model gpt-5.5
```

### Codex FFF MCP proxy

サポートする形は1つだけです。

```toml
[mcp_servers.fff]
command = "/Users/akitani/.local/bin/tokgain"
args = ["mcp-proxy", "--agent", "codex", "--tool", "fff", "--base-path", "/Users/akitani/_dev", "--", "/opt/homebrew/bin/fff-mcp", "/Users/akitani/_dev"]
```

`tokgain-mcpproxy` や `mcpproxy` alias はありません。設定の分岐を増やさないためです。

### Hermes hook

Hermes は plugin hook から `tokgain observe ...` を呼べます。対象例:

- `h5i ...` の terminal output
- `ast-grep ...` / `sg ...` の terminal output
- FFF MCP tool result

記録先は同じ `events.jsonl` です。

### h5i / FFF の測定

native ledger が無い場合は baseline-vs-optimized で測ります。

```bash
tokgain measure h5i --cmd 'pytest -q'
tokgain measure fff --path . --query 'PrepareUpload'
```

`measure h5i` は raw と `h5i capture run` でコマンドを2回実行します。副作用のない test/build/search/log 確認に限定してください。

### 月次レポート例

`tokgain report --period month` は Slack / cron / release note に貼りやすい短い出力を返します。

```text
2026-06 saved=1594 tokens usd=$0.007970 events=1 errors=0
  demo: 1594 tokens $0.007970 (1 events)
```

構造化して使う場合:

```bash
tokgain report --period month --json
```

### PyPI 公開状態

package metadata と GitHub Actions の publish workflow は PyPI-ready です。初回 PyPI release までは GitHub 経由の `uvx` を使います。

```bash
uvx --from git+https://github.com/2001Y/tokgain tokgain --help
```

PyPI 公開後は短い形で使えます。

```bash
uvx tokgain --help
uv tool install tokgain
```

## 開発

```bash
git clone https://github.com/2001Y/tokgain.git
cd tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m tokgain.cli --help
```

build / packaging check:

```bash
uv build
uvx twine check dist/*
uvx --from . tokgain --help
```

## 方針

- append-only local ledger first
- 失敗は黙殺しない
- 実体ツールを呼び、似た実装を作らない
- raw prompt/tool output は原則保存しない
- integration ごとにコマンド経路を1つに固定する

## License

MIT

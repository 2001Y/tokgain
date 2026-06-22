# tokgain

`tokgain` は、RTK / headroom / lean-ctx / h5i / fff などの token 節約量を、ローカルの JSONL に追記して後から集計する小さなCLIです。

名前は `token` + `gain`。Codex専用名にせず、Claude Code などにも展開しやすい短い名前にしました。

## Install

```bash
cd /Users/akitani/_dev/tokgain
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/tokgain --help
```

必要なら PATH に追加します。

```bash
export PATH="/Users/akitani/_dev/tokgain/.venv/bin:$PATH"
```

## Quick start

```bash
tokgain collect --tool auto --model gpt-5.5
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
tokgain collect [--tool auto|all|rtk|headroom|lean-ctx|h5i|fff] [--date YYYY-MM-DD] [--model MODEL]
tokgain report --period day|week [--date YYYY-MM-DD] [--json]
tokgain show [--tool rtk] [--status ok|error] [--limit N]
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

`fff` の公式実体は `fff-mcp` MCP server です。ファイル検索を高速・省コンテキスト化しますが、現時点では native な savings ledger を出さないため、`tokgain` は存在しない `fff stats` のようなコマンドを推測実行しません。fff の節約量を集計したい場合は、外部ベンチ結果を `TOKGAIN_FFF_FILE` または `~/.fff/savings.jsonl` に1行1イベントで置きます。

`collect --tool auto` は利用可能な source だけ読みます。明示指定した tool が失敗した場合は ERROR event を残して終了コード `1` です。

## launchd

テンプレートだけ同梱しています。登録する場合:

```bash
cd /Users/akitani/_dev/tokgain
scripts/install-launchd.sh
```

毎日 0:00 に `tokgain collect --tool auto` を実行します。

## Development

```bash
cd /Users/akitani/_dev/tokgain
pytest -q
python -m tokgain.cli --help
```

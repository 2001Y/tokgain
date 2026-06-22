# tokgain

`tokgain` は、RTK / headroom / lean-ctx / h5i などの token 節約量を、ローカルの JSONL に追記して後から集計する小さなCLIです。

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
tokgain collect [--tool auto|all|rtk|headroom|lean-ctx|h5i] [--date YYYY-MM-DD] [--model MODEL]
tokgain report --period day|week [--date YYYY-MM-DD] [--json]
tokgain show [--tool rtk] [--status ok|error] [--limit N]
tokgain export --format jsonl|json
tokgain doctor [--json]
tokgain prices show
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

デフォルトの価格表は placeholder です。実運用では手で更新した JSON を渡してください。

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
| headroom | `TOKGAIN_HEADROOM_FILE`, `~/.headroom/proxy_savings.json`, `headroom stats --json` |
| lean-ctx | `TOKGAIN_LEAN_CTX_FILE`, `~/.lean-ctx/savings.jsonl`, `lean-ctx gain --json` |
| h5i | `TOKGAIN_H5I_SUMMARY_FILE`, `~/.h5i/savings.jsonl`, `h5i stats --json` |

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

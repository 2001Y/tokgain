# tokgain 計画

## 決定

- CLI名: `tokgain`
  - `token` + `gain`。短く、Codex / Claude Code / Hermes など特定サービスに寄せすぎない。
  - `collector` より短く、日常実行コマンドとして覚えやすい。
- Project folder: `/Users/akitani/_dev/tokgain`
- 正本: `~/.local/state/tokgain/events.jsonl` の単一JSONL
- 派生物: `~/.local/state/tokgain/daily/YYYY-MM-DD.json`
- API換算: ccusage と同様に LiteLLM `model_prices_and_context_window.json` を主に使い、`models.dev/api.json` で補完する。未知モデルは `price_missing=true` として `usd_saved_estimate=0.0` を残す。

## 目的

`rtk` / `headroom` / `lean-ctx` / `h5i` などが報告する token 節約量を、Prometheus / Grafana なしで、ローカルCLIとファイルだけで後から確認・集計できるようにする。

## 非目標

- 外部SaaS送信
- リアルタイム可視化
- 厳密な重複排除
- 高機能ダッシュボード

初版では `reported_total` を主要値とし、`net_total` は `null` のまま将来拡張に残す。

## データ正本

```text
~/.local/state/tokgain/
  events.jsonl              # 正本。1行1イベント
  daily/YYYY-MM-DD.json     # 派生の日次要約
  state.json                # 最終成功/失敗状態
```

イベントは必ず以下を持つ。

- `schema_version`
- `status`: `ok` / `error`
- `ts`
- `period`
- `tool`
- `layer`
- `model`
- `saved_tokens`
- `saved_input_tokens`
- `saved_output_tokens`
- `estimate_mode`
- `usd_saved_estimate`
- `price_table_version`
- `source_ref`
- `session_id`
- `incomplete`
- `exclude_from_totals`

`model` が取れない場合は `model_missing` とし、`incomplete=true`, `exclude_from_totals=true` にする。

## model 解決順

1. `collect --model`
2. `collect --metadata` または `TOKGAIN_SESSION_METADATA` の JSON
3. adapter payload 内の `model`
4. 環境変数: `TOKGAIN_MODEL`, `CODEX_MODEL`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `ANTHROPIC_MODEL`
5. `model_missing`

## 価格テーブル

既定は live 取得です。明示的に `--prices` / `TOKGAIN_PRICES` がある場合のみ手動JSONを使う。
取得ロジックは ccusage に寄せ、LiteLLM を主ソース、models.dev を不足分の補完ソースにする。
取得結果は `~/.cache/tokgain/prices.json` に保存し、`--offline-prices` では cache または package 内 `src/tokgain/data/prices.json` を使う。

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

## Adapter 方針

### rtk

- 優先: `rtk gain --all --format json`
- JSON中の `daily[].date` が対象日と一致する行のみ event 化する。

### headroom

- 優先: `TOKGAIN_HEADROOM_FILE`
- 次点: `~/.headroom/proxy_savings.json`
- 次点: `headroom perf --format json`

### lean-ctx

- 優先: `TOKGAIN_LEAN_CTX_FILE`
- 次点: `~/.lean-ctx/savings.jsonl`, `~/.lean-ctx/savings.json`
- 次点: `lean-ctx gain --json`, `lean-ctx savings export`

### h5i

- 優先: `TOKGAIN_H5I_SUMMARY_FILE`
- 次点: `~/.h5i/savings.jsonl`, `~/.h5i/savings.json`, `~/.h5i/summary.json`

`h5i capture run` は任意コマンド実行が必要なので、collector は勝手に実行しない。`h5i stats` のような global stats は無いので、capture/export由来のJSON/JSONLだけを読む。

### fff

- 公式実体: `fff-mcp` MCP server。公式推奨インストールは `brew install dmtrKovalenko/fff/fff-mcp` または公式 install script。
- 優先: `TOKGAIN_FFF_FILE`
- 次点: `~/.fff/savings.jsonl`, `~/.fff/savings.json`, `~/.fff/stats.json`
- `fff-mcp` はファイル検索を高速・省コンテキスト化するが、現時点では native な savings ledger を出さない。
- そのため collector は存在しない `fff stats` / `fff savings` を推測実行しない。fff の節約量は外部ベンチ/エクスポートが作ったJSON/JSONLだけを正本として読む。

## CLI

```bash
tokgain collect --tool auto --model gpt-5.5
tokgain report --period day --date 2026-06-21
tokgain report --period week --date 2026-06-21
tokgain show --tool rtk
tokgain export --format jsonl
tokgain doctor
tokgain prices show
tokgain prices refresh
```

## エラー方針

- adapter失敗時は `ERROR` event を `events.jsonl` に追記する。
- `collect --tool auto` は存在する source のみ読む。
- 明示指定した adapter が失敗した場合、event を残して終了コード `1`。
- `--allow-errors` 指定時だけ終了コード `0`。

## 実装済み検証

- `pytest -q`
- `python -m tokgain.cli --help`
- `tokgain collect` の JSONL/daily/state 生成
- `tokgain report/show/export/prices/doctor`
- rtk実コマンド `rtk gain --all --format json` の出力形状確認

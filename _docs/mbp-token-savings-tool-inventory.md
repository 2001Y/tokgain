# MBP token / context saving tool inventory

収集日: 2026-06-22

## 調査対象

- `/Users/akitani/.codex/AGENTS.md`
- `/Users/akitani/.codex/config.toml`
- `/Users/akitani/.hermes/config.yaml`
- `/Users/akitani/.config/opencode/*` と chezmoi 正本
- `/Users/akitani/.codex/skills`, `/Users/akitani/.hermes/skills`
- PATH 上の実バイナリ
- `codex mcp list`, `hermes mcp list`, `claude mcp list`

## 見つかった実体

| tool / service | 状態 | レイヤー | tokgain での扱い |
|---|---|---|---|
| `rtk` 0.42.0 | PATHあり、Hermes `rtk-rewrite` 有効 | terminal output | `collect --tool rtk` |
| `headroom` 0.27.0 | PATHあり | proxy / bundled tools | `collect --tool headroom` + 必要なら `bench` |
| `lean-ctx` 3.8.11 | PATHあり | file/context representation | `collect --tool lean-ctx` |
| `h5i` 0.2.1 | PATHあり | recoverable raw-output wrapper | 外部JSONLを `collect --tool h5i`、任意比較は `bench` |
| `fff-mcp` 0.9.6 | Codex/Hermes MCP 有効 | file search MCP | 外部JSONLを `collect --tool fff`、MCP結果比較は `bench --tool fff` |
| `rg` 15.1.0 | PATHあり | grep/search output | `bench --tool rg` |
| `ast-grep` / `sg` | 直接PATHなし。`headroom sg` として利用可能 | AST search / outline | `bench --tool ast-grep --baseline-cmd ... --optimized-cmd 'headroom sg ...'` |
| `scc` / `difft` | 直接PATHなし。headroom bundled registry にあり | code stats / structural diff | `bench` |
| `claude` 2.1.114 | PATHあり。MCP未設定 | agent runtime | usage/savingsログが取れたら `bench` または将来adapter |
| `codex` 0.135.0 | PATHあり。MCPあり | agent runtime | `tokgain` 実装対象。native usageは将来adapter |
| `hermes` 0.16.0 | PATHあり | agent runtime | Hermes側 usage は別系統。圧縮前後出力は `bench` |
| `coderabbit` 0.3.4 | PATHあり | code review | review入力/出力の比較は `bench --tool coderabbit` |
| `tokensaver` | npm package名は存在するが未導入。GitHub検索では明確な主要repo無し | duplicate/context reduction | 導入後、出力ペアを `bench --tool tokensaver` |
| `contextmode` | PATH/npm/GitHub検索では未検出 | unknown context mode | 実体確認後、出力ペアを `bench --tool contextmode` |
| `codereviewgraph` / `code-review-graph` | PATH/npm/GitHub検索では未検出 | unknown code-review graph | 実体確認後、出力ペアを `bench` |

## MCP / skill から見た節約候補

### Codex

- MCP: `fff`, `serena`, `context7`, `deepwiki`, `drawio`, `computer-use`, `node_repl`。
- `AGENTS.md` は git 管理ディレクトリでは `fff` tools (`fffind`, `ffgrep`, `fff-multi-grep`) 優先を指示済み。
- Skills:
  - `coding-confidant`: repomix + `--compress` + input budget。大きいrepo送信前の token 削減候補。
  - `web-research`: 親コンテキストへ raw fetch を入れず EvidencePacket のみ返す。
  - `agent-browser`: compact snapshot により DOM 全量送信を避ける。

### Hermes

- MCP: `fff` 有効。
- Config: `rtk-rewrite` plugin 有効。paste collapse / context compression 設定あり。
- Hermes profile の skills には `token-compression-agent-tools` があり、RTK/headroom/lean-ctx/h5i/fff の比較・運用を保持。

### Claude Code

- `claude` CLI は存在するが `claude mcp list` は未設定。
- 今回の `tokgain` は CLI名・schemaを Claude Code に寄せず、`bench --tool claude` や将来 adapter で吸収できる形にする。

## 実装判断

1. Native ledger がある/外部JSONLがあるものは `tokgain collect` adapter で読む。
2. Native ledger が無いものは、存在しない stats コマンドを推測せず `tokgain bench` で baseline / optimized の出力を比較する。
3. `events.jsonl` は1つのまま。`bench` も同じ event schema に正規化する。
4. `saved_tokens` が負数でも残す。効果が無い/悪化したケースも後から集計できるようにする。
5. 自然利用の hook / MCP proxy 経由 event は `observed_call_count`, `duration_ms`, `turn_id`, `tool_call_id`, `api_request_id` を保存する。token 節約量だけでなく「何コール使って得た節約か」を report JSON の `observed_call_count` / `unique_*_count` で確認する。
6. 2026-07-01 時点の実機確認: `/Users/akitani/.local/bin/tokgain` が壊れた symlink になっていたため、Hermes plugin と Codex MCP proxy が実行できない状態だった。`uv tool install --reinstall /Users/akitani/_dev/tokgain` で復旧し、Hermes plugin は binary 不在時も `python -m tokgain.cli` へ fallback する。

## 代表コマンド

```bash
# rg の出力削減効果
tokgain bench --tool rg --layer search-output --model gpt-5.5 \
  --baseline-cmd 'rg "TODO|FIXME" .' \
  --optimized-cmd 'rg "TODO|FIXME" . --glob "!node_modules" | head -80'

# ast-grep/headroom sg の構造出力を raw grep と比較
tokgain bench --tool ast-grep --layer ast-search --model gpt-5.5 \
  --baseline-cmd 'rg "def |class " src tests' \
  --optimized-cmd 'headroom sg run --pattern "def $F($$$ARGS): $$$BODY" --lang python src tests'

# MCPやClaude/Codexなど、直接コマンドで比較できないものはファイルで比較
tokgain bench --tool fff --layer file-search-mcp --model gpt-5.5 \
  --baseline-file /tmp/raw-rg-output.txt \
  --optimized-file /tmp/fff-mcp-result.txt
```

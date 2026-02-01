# daily-research-agent — AGENTS.md

このファイルは、Codex / Claude Code / Cursor / Copilot などの **AIエージェントがこのリポジトリを安全かつ一貫して開発するためのルール**です。
（AGENTS.md の仕様/思想は https://agents.md/ を参照）

この `AGENTS.md` は **リポジトリ全体**に適用され、サブディレクトリに追加された `AGENTS.md` がある場合はその指示が優先されます。

## 0. このプロジェクトの前提（必読）

- プロジェクト概要・スコープの一次情報は `docs/requirements.md`。
  - 実装・設計の判断に迷ったら、必ずここに立ち返る。
- 詳細設計の一次情報は `docs/design.md`。
  - 実装で迷ったら、まず設計に合わせる（独自流儀に逸れない）。
- 初期目標は「ローカルで定時リサーチ→Markdown記事生成」まで（デプロイやUIは対象外）。

## 1. ゴールデンルール（最重要）

- 依存管理は **uv** のみ（`python` / `pip` コマンドは禁止）。
  - 追加は `uv add ...`、実行は `uv run ...` を使う。
- 変更は **最小差分**で、要求範囲外の改修・リネーム・整形をしない。
- 秘密情報（APIキー、トークン、Cookie、個人情報）を **コミットしない**。
  - シークレットは `.env`（環境変数）に置き、設定値は `configs/agent.toml` に置く。
  - `.env` は Git 管理しない（雛形は `.env.example`）。
- 依存の再現性を優先する。
  - `uv.lock` を導入したら Git 管理し、実行環境の差分を減らす。
- 外部情報（Web / X 等）を扱う場合は **過剰転載を避け**、URL等の出典を残す。

## 2. 作業の進め方（AIエージェント向け）

- まず `docs/requirements.md` と既存コードを読み、作業単位を小さく切って進める。
- 追加ファイルを作るときは、後から探索しやすい命名と配置にする（例: `src/`, `docs/`, `scripts/` など）。
- 設計を変える場合は、必ず `docs/design.md` / `configs/agent.toml` / `templates/*.toml` を整合させる。
- 生成物・状態は **コードと分離**する。
  - 記事/調査メモ: `outputs/`（Git管理しない）
  - 実行間状態（例: Xブックマークキャッシュ）: `state/`（Git管理しない）

## 3. よく使うコマンド（uv）

- 依存追加: `uv add <package>`
- 実行: `uv run <command>`
- テスト: `uv run pytest`（導入されている場合）
- Lint/Format: `uv run ruff check .` / `uv run ruff format .`（導入されている場合）

## 4. 実行方法（運用前提）

- Docker は **常駐しない**。単発実行に寄せる（`docker compose run --rm`）。
  - ビルド: `docker compose build`
  - 実行（例）: `docker compose run --rm app uv run daily-research-agent run --preset daily_ai_news --date 2026-02-01`

## 5. 設計・実装ガイド（安定運用）

- CLI前提：ユーザーが「コマンド1つ」で記事Markdownを生成できる体験を優先。
  - CLIを追加する場合は `pyproject.toml` の `[project.scripts]` を使い、`uv run <script>` で呼べる形にする。
- CLI は `--topic` を持たず、**`--preset` + TOML** で運用する（プロンプトは日々育てる前提）。
- “エージェンティック”要件：不足情報の検知→追加調査→統合→出力、の流れが追える構造にする。
  - 可能なら「参照ソース」「調査メモ」「採用/非採用理由」を後から辿れる形で残す。
- LLM は **OpenRouter に統一**し、モデルは OpenRouter の model ID（例: `openai/gpt-5.2`）を `configs/agent.toml` で切替する。
  - `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` は `.env` で管理する（OpenAI/Anthropic の直APIキー前提に戻さない）。
- 暴走防止（必須）：`max_web_queries` 等の上限は **CLI ではなく TOML**（`configs/agent.toml`）で管理する。

## 6. 外部連携（コスト/権利/安全）

- ファイル操作はこのリポジトリ配下に限定し、意図しない読み書きを避ける。
- X上のコンテンツは規約・権利に配慮し、記事には短い引用・要約・URL参照を基本とする。
- X ブックマークはコストがかかるため、**再取得を最小化**する。
  - 取得済みは `state/` のキャッシュに保存し、同一ポストはなるべく再取得しない（設計: `docs/design.md`）。
  - 引用ポストは引用元も辿れるようにする（深さ制御）。
- MCP server の接続情報は `configs/agent.toml` に集約する（`.env` はキー等のシークレットのみ）。

## 7. 観測 / ログ（改善サイクル）

- LangSmith を最初から有効化できるようにし、run のメタデータ（preset/date/取得件数など）を Trace に残す。
- LangSmith とは別に **Python logger** でローカルログを保存する（run ごとの `outputs/runs/<run_id>/app.log` を想定）。

## 8. AGENTS.md の運用（追加ルールの置き場所）

- ディレクトリごとに追加ルールが必要になったら、その配下に `AGENTS.md` を追加する。
  - 例: `src/AGENTS.md`（コード規約）、`docs/AGENTS.md`（文章規約）など
- 競合する場合は「より近いディレクトリの `AGENTS.md`」を優先する（agents.md の推奨に従う）。

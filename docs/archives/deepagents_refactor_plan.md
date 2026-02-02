# deepagents-first 再設計・リファクタ計画（todo + subagent + filesystem）

最終更新: 2026-02-01

## 目的（ゴール）

現状の「Python Orchestrator が Researcher→Writer を直列実行する」形から、deepagents の思想に合わせて **todo（`write_todos`）+ サブエージェント（`task`）+ ファイル中心（filesystem tools / backend）** を前提としたアーキテクチャへ移行する。

併せて、以下を満たす。

- **すべてのLLM向けプロンプト/指示文は TOML で管理**し、Python からは参照・レンダリングするだけにする
  - 例: system prompt、ユーザー入力テンプレ、サブエージェント定義、スキル本文（後述）
- 既存の運用要件（`configs/agent.toml` + `.env`、`outputs/`/`state/` 分離、LangSmith観測）を維持する

## 現状整理（As-Is）

現状は deepagents を「実行エンジン」として使ってはいるが、deepagents の中核（todo/subagent/filesystem を前提にした “harness” 的な組み立て）をまだ十分に活かせていない。

### 現行フロー（要約）

- CLI → `load_config()` → `resolve_preset()` → `run_orchestrator()`（`src/daily_research_agent/cli.py` / `src/daily_research_agent/orchestrator.py`）
- Researcher: `create_deep_agent(... tools=mcp_tools ...)` を 1回 `ainvoke`
- Writer: `create_deep_agent(... tools=[] ...)` を 1回 `ainvoke`

### 痛点（Why change）

- deepagents の **`write_todos` / `task`** をアプリ設計としては使っていない（LangSmith上は内部ステップが見えるため複雑に見えるが、アプリは直列パイプライン）
- Orchestrator 内に **LLM向けの指示文が一部ハードコード**されている（例: Research/Write の HumanMessage 冒頭文）
- プロンプトが Python（`domain/prompts.py` + orchestrator）と TOML に分散しており、調整や新規エージェント追加の導線が弱い

## To-Be 方針（deepagents ベストプラクティスに寄せる）

deepagents ドキュメント（overview / harness / customization / middleware / backends / skills / reference）を前提に、下記を設計の軸にする。
（deepagents は built-in system prompt + harness で「todo / filesystem / subagent」が標準で入るため、アプリ側はそれを活かす組み立てに寄せる）

**重要（設計の前提）:**

- `create_deep_agent(system_prompt=...)` の `system_prompt` は、deepagents の built-in system prompt を前提に **追加の指示（steering）**を与える用途に寄せる（合成順序などの詳細は deepagents 実装に従う）。よってアプリ側は **重複しがちな説明を system prompt にベタ書きしない**（skill へ逃がす）。
- セキュリティは「LLMの自制」に期待しない。**backend / sandbox / middleware / tool allowlist** で境界を強制する（特に filesystem / 外部実行系）。

### （補足）deepagents の built-in tools / middleware を前提にした設計にする

- built-in tools（要点）:
  - todo: `write_todos` / `read_todos`
  - filesystem: `ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep`
  - subagent: `task`
- built-in に含まれない操作（例: 任意コマンド実行）は、**必要性が出てから**カスタム tool として明示的に追加し、許可範囲（allowlist）と監査ログを必須にする。
- 標準 middleware stack（要点）:
  - todo / filesystem / subagent / summarization などが標準で入るため、アプリ側は「これを使う」前提で artifact contract・skills・subagents を設計する

### 1) 「1つの Supervisor deep agent」+ `task` サブエージェントへ

- アプリ側の Orchestrator は「準備と検証」に寄せる（run_id払い出し、入力ファイル準備、実行、成果物検証/保存）
- 実際の思考・分解・委譲は Supervisor に寄せ、必要に応じて `task` で Researcher/Writer/Verifier を呼ぶ
- サブエージェント設計のベストプラクティス:
  - `description` は具体的に（Supervisor が委譲判断する材料になる）
  - `tools` は最小に（権限を絞る）
  - `name` はトレース/監査ログの識別子として固定する（LangSmith では `lc_agent_name` メタデータとして区別できる）

### 2) 「ファイル契約（artifact contract）」を中心にする

deepagents の filesystem tools は、長文の調査結果・中間メモをファイルへ退避し、コンテキスト破綻を避ける思想が強い。
よって「どのファイルを input とし、何を output として残すか」を契約化し、品質とデバッグ容易性を上げる。

例（案）:

- 入力（アプリ側が用意）:
  - `/artifacts/inputs/bookmarks.json`
  - `/artifacts/inputs/template.toml`
  - `/artifacts/inputs/run.json`（run_id / date / preset / 制約など）
- 中間生成物（エージェントが書く）:
  - `/artifacts/research/findings.json`
  - `/artifacts/research/sources.json`
  - `/artifacts/research/memo.md`
  - `/artifacts/draft/article.md`
- 最終成果物（エージェントが書く）:
  - `/artifacts/final/article.md`（Runner が `outputs/articles/...` に配置）

### 3) Backend は “安全に” 使う（Composite + virtual_mode）

- デフォルトは `StateBackend`（ephemeral）にして、エージェントの scratch を軽く保つ
- 永続化したい領域だけを `FilesystemBackend(virtual_mode=True)` にルーティングする（`CompositeBackend`）
  - `/artifacts/` だけがディスクへ、など
- これにより「deepagents のファイル中心」を活かしつつ、**意図しないファイルアクセス範囲**を最小化する

### 4) プロンプト/エージェント定義は TOML を “唯一の真実” にする

deepagents 自体の built-in system prompt はライブラリ側だが、**アプリ固有の指示文は全てTOML化**する。

- Supervisor system prompt
- サブエージェント（Researcher/Writer/Verifier...）の prompt と説明
- 初期ユーザーメッセージ（=「今回のタスク」テンプレ）
- スキル本文（後述: deepagents の skills を使う場合）

## 新アーキテクチャ（To-Be）

### 0) Runner（Python）の責務

Runner は「準備」「実行」「検証/保存」に専念する。

1. run_id 生成、`outputs/runs/<run_id>/` 作成
2. X ブックマーク取得（既存実装を利用）→ `bookmarks.json` を `/artifacts/inputs/` へ配置
3. MCP tools 接続（既存実装を利用）→ allowlist/denylist に従い tools を選別
4. deepagents の Supervisor を `create_deep_agent` で構築（TOML定義から）
5. Supervisor を 1回 invoke/ainvoke
6. `/artifacts/final/article.md` 等の **契約ファイル**が存在するか検証し、`outputs/articles/YYYY-MM-DD/...md` に配置
7. `run.json` を更新し、LangSmithタグ/metadata と一致する run 情報を残す

### 1) deepagents Supervisor の役割

Supervisor は以下を system prompt + スキルで担保する。

- `write_todos` で計画（todo）を書き、段階的に進める
- Researcher/Writer/Verifier を `task` で呼び出す（サブエージェント定義は TOML）
- すべての中間成果物/最終成果物を **ファイルとして保存**する（artifact contract）
- 失敗時は `/artifacts/run/diagnostics.md` などに理由と次のアクション案を残す

## TOML 設計案（設定でエージェントを増やせる）

「どんなエージェントを、どのモデル・どのツール・どの prompt で動かすか」を TOML に寄せる。

### 例: 設定構造（案）

`configs/agent.toml`（または `configs/agent.toml` から参照される `templates/agents.toml`）に、以下を追加する。

- `[deepagents]`（harness設定）
  - `implementation`（`create_deep_agent` / `custom_middleware_stack`）
  - `tool_token_limit_before_evict`（FilesystemMiddleware の閾値。※`create_deep_agent` だけでは細かい調整が難しい場合があるため、必要なら `custom_middleware_stack` で明示的に FilesystemMiddleware を構築する）
  - `backend`（`state` / `composite` / ルーティング定義）
  - `interrupt_on`（必要なら）
- `[prompts]`（既存）
  - `registry`（prompt本文を集約: `prompts.registry.<id>.text`）
- `[agents.supervisor]`
  - `model` / `prompt_id` / `skills` / `memory_files`
  - `tools.allow` / `tools.deny`
  - `subagents = ["researcher", "writer", "verifier"]`
- `[agents.subagents.<name>]`
  - `description`
  - `model`
  - `prompt_id`
  - `tools.allow` / `tools.deny`

### prompt のレンダリング（案）

すべての prompt は TOML に置き、Python 側で以下を埋めるだけにする。

- `{date}`（YYYY-MM-DD）
- `{timezone}`
- `{preset_name}`
- `{max_web_queries}`
- `{artifact_paths.*}`（契約パス）
- `{sources.daily_sites}`（必要なら整形して注入、もしくはファイルにして読ませる）

※埋め込みは「不足変数があればエラー（fail-fast）」にし、事故を減らす。

## skills の扱い（TOML管理を守りつつ deepagents の利点も取る）

deepagents の skills は「必要な時だけロードされる」ため、system prompt の肥大化を防げる。
一方で、skill は通常 `SKILL.md` 等のファイルとして扱うため、「すべてのプロンプトはTOML」を満たすには工夫が必要。

提案（推奨順）:

1) **TOMLを真実にして、invoke 時に StateBackend へ skill を seed する（推奨）**
   - deepagents は StateBackend（デフォルト）利用時、`invoke(files={...})` で仮想FSへファイルを投入できる（skills の progressive disclosure と相性が良い）。
   - `prompts.registry.<skill_id>.text` を `files={"/skills/<skill_id>/SKILL.md": "...", ...}` として投入し、`skills=["/skills/"]` を渡す。
   - 永続化が不要な “手順書/ルール” は state に置き、run 成果物は `/artifacts/`（FilesystemBackend）へ書く、という住み分けができる。

2) **TOMLを真実にして、run 開始時に skill ファイルを生成**する
   - `prompts.registry.<skill_id>.text` を `outputs/runs/<run_id>/skills/<skill_id>/SKILL.md` に書き出す
   - `create_deep_agent(skills=[...])` へそのディレクトリを渡す（FilesystemBackend 前提）

3) スキルは例外として `skills/` 配下のファイル管理（TOML要件を緩める）
   - 運用は簡単だが、要件に反するため本計画では 1) を推奨

## ローカルログ強化（監査ログ / セキュリティ）

方針:

- 詳細なトレース（内容）は LangSmith に任せるが、**監査・運用のために “全アクションのメタデータ” をローカルへ必ず残す**。
- 「内容（送信・受信本文）」は保存しない（プロンプト/検索クエリ/取得本文/生成本文など）。
- 代わりに、**誰が（どのエージェントが）何を（どのツール/どのファイル操作を）いつ、どれくらい、成功したか**を機械可読で残す。

### 監査ログに残すイベント（例）

最低限、以下は 1イベント=1行JSON（JSONL）で記録する。

- run lifecycle: `run_started` / `run_finished`（run_id、preset、date、git_sha、設定ハッシュ、所要時間）
- X: `x_fetch_started` / `x_fetch_finished`（件数、cache利用有無、HTTPステータス、リトライ回数）
- MCP: `mcp_connect_started` / `mcp_connect_finished`（server名、tool数、失敗理由）
- agent: `agent_invoke_started` / `agent_invoke_finished`（agent名、subagent名、tags、metadata、所要時間、成功/失敗）
- tools（LangChain callback で拾う）:
  - `tool_started` / `tool_finished`（tool名、引数の“形”だけ、所要時間、成功/失敗、結果サイズ）
  - filesystem tools: 対象パス、offset/limit、writeサイズ、edit件数、glob/grep件数
  - `task`（subagent起動）: name、所要時間、成功/失敗
  - `write_todos`（計画更新）: todo件数・ステータス集計（本文は保存しない）
- LLM（任意だが推奨）:
  - `llm_started` / `llm_finished`（model id、max_tokens、所要時間、usageメタデータ（取得できる範囲で））

### 実装アプローチ

- deepagents / LangChain は callback で tool/LLM の start/end を捕捉できるため、**内容を捨てた監査ログ用 CallbackHandler** を実装し、`invoke/ainvoke` の `config={"callbacks":[...], ...}` で全 run に注入する。
- 追加で、deepagents 外側（X / MCP 接続 / 出力ファイル移動など）は Python 側で明示的に `audit_event(...)` を出す。
- 監査ログは `outputs/runs/<run_id>/audit.jsonl` を推奨（`app.log` と分離し、集計/転送しやすくする）。

### 設定（TOML化）

`configs/agent.toml` に例として以下を追加する（最終形はPhase 0で詰める）。

- `[logging.audit]`
  - `enabled = true`
  - `path = "../outputs/runs/{run_id}/audit.jsonl"`（テンプレ）
  - `log_llm_events = true`
  - `log_tool_events = true`
  - `redaction = "strict"`（内容は捨てる）

## 追加の改善（この機会にやる）

監査・安全・運用を考えると、deepagents-first への移行と同時に入れておく価値が高い。

### 1) `max_web_queries` を “プロンプト” ではなく “強制” にする

現状は prompt に書くだけで、ツール層で強制していない。
コスト/暴走防止のため、以下のいずれかで **ハードリミット**を導入する。

- MCP tools をラップして、呼び出し回数カウンタで超過時に例外（= run を止める or soft-fail して memo に残す）
- middleware / callback ベースで tool 呼び出し回数を監視して強制停止

### 2) MCP の子プロセスへ “環境変数を全部渡す” のをやめる

現状は stdio MCP サーバ起動時に `os.environ` を丸ごと渡しているため、不要なシークレットが子プロセスへ伝播し得る。
`configs/agent.toml` 側で allowlist（例: `PATH`, `HOME`, `PERPLEXITY_API_KEY` など必要最小限）を定義し、継承を最小化する。

### 3) 失敗時の診断ファイル（/artifacts/run/diagnostics.md）を標準化

LangSmith が見られない環境でも、最低限の原因・次のアクションが分かるようにする。

- 例: どのフェーズで止まったか、MCP接続の可否、X取得の可否、出力ファイルの有無、リトライ回数、タイムアウト等

### 4) `observability.langsmith.enabled` を実際の tracing ON/OFF に反映

現状は config で enabled/project を読んでいるが、tracing の実効 ON/OFF は環境変数依存になっている。
設計と実装を一致させる（例: config を優先して `LANGSMITH_PROJECT` をセットし、enabled=false の場合は tracing を抑止/警告する）。

### 5) “出力の検証” を入れる（artifact contract + 最低品質チェック）

- `/artifacts/final/article.md` が存在するか
- Markdown の末尾に references（URL）があるか
- `sources.json` が空なら “不確実性” の明記があるか、など

## 実装ステップ（段階移行）

安全に進めるため「挙動を保ったまま段階的に deepagents-first へ」移行する。

### Phase 0: 監査ログ（ローカル）基盤を追加

- `audit.jsonl`（run単位）を導入し、run lifecycle / X / MCP / agent invoke のメタデータを出す
- LangChain callback を使い、tool/LLM の start/end メタデータを `audit.jsonl` に書く（内容は保存しない）
- **受け入れ条件**: 1回の run で「どのツールが何回呼ばれ、成功/失敗と所要時間が分かる」ログが残る

### Phase 1: “プロンプトのTOML一元化” を先に完了

- `orchestrator.py` にある HumanMessage の固定文言を TOML へ移動
- `domain/prompts.py` の Researcher/Writer system prompt も TOML テンプレ化（renderで埋める）
- **受け入れ条件**: Python コードから LLM向け指示文が消える（もしくは最小のプレースホルダ展開のみ）

### Phase 2: “ファイル契約” への移行（まだ2エージェントでもOK）

- ブックマークJSON/テンプレ/設定を「メッセージ本文にインライン注入」ではなく「ファイルとして配置→read_fileで読む」形にする
- 既存の `research.md` / `sources.json` / 記事出力は維持しつつ、deepagents の filesystem を仕事の中心に移す
- **受け入れ条件**: 主要入力が `/artifacts/inputs/*` に揃い、エージェントがそれを参照して生成する

### Phase 3: Supervisor + subagents（`task`）へ統合

- Supervisor を新設し、Research/Write を `task` で委譲する形へ変更
- `create_deep_agent(subagents=...)` を TOML 定義から生成
- **受け入れ条件**: top-level invoke は 1回、Research/Write が subagent として LangSmith にぶら下がる

### Phase 4: Backend を Composite に（安全 + 拡張）

- `CompositeBackend(default=StateBackend, routes={"/artifacts/": FilesystemBackend(..., virtual_mode=True)})` の導入
- “書き残すべきもの” は `/artifacts/` に集約し、scratch は state に逃がす
- **受け入れ条件**: エージェントが repo ルート等に触れず、run_dir 配下だけが永続化される

### Phase 5: skills 生成（TOML → SKILL.md）と運用改善

- Research/Write/Verify の “手順書” を skill 化して progressive disclosure を効かせる
- 例: 引用・出典ルール、採用/非採用理由の書き方、記事テンプレの使い方、toolの使い分け

### Phase 6: optional（将来拡張）

- 長期メモリ（`StoreBackend` + `/memories/`）の導入
- HITL（`interrupt_on`）の “debugモード” 導入（普段はオフ）
- LangSmith Dataset/Eval による品質回帰テスト

## “ハードコード撲滅” の対象（棚卸し指針）

以下を「LLM向けテキスト」とみなして TOML へ集約する。

- system prompt 文字列（Researcher/Writer/Supervisor）
- user message のテンプレ文（開始指示、JSON出力強制、ファイル契約の説明）
- “出力フォーマットの仕様”（JSON schema / Markdown要件）
- エラー時の挙動ルール（失敗の書き方、fallback）

## 受け入れ条件（Definition of Done）

- `configs/agent.toml`（+ templates）だけで
  - エージェント定義（Supervisor + subagents）
  - それぞれの prompt/skill 文面
  - 使うツールの許可/禁止
  - ファイル契約（入出力パス）
  - 主要パラメータ（max_web_queries 等）
  を変更できる
- Python コード中に LLM向けの固定文が残らない（例外はプレースホルダレンダリングの最小実装のみ）
- 1回の Supervisor 実行で todo → task → ファイル出力が完結する
- `outputs/runs/<run_id>/` に調査メモ・ソース・記事が揃い、再現と改善ができる

## 参考リンク（一次情報）

- （ローカル同梱）`docs/deepagents/overview.md`
- （ローカル同梱）`docs/deepagents/harness.md`
- （ローカル同梱）`docs/deepagents/middleware.md`
- （ローカル同梱）`docs/deepagents/subagents.md`
- （ローカル同梱）`docs/deepagents/agent-skills.md`
- （ローカル同梱）`docs/deepagents/backends.md`
- （ローカル同梱）`docs/deepagents/human-in-the-loop.md`
- （ローカル同梱）`docs/deepagents/long-term-memory.md`
- Deep Agents overview: https://docs.langchain.com/oss/python/deepagents/overview
- Reference: https://reference.langchain.com/python/deepagents/

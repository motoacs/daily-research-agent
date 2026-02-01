# X OAuth2（PKCE）アクセストークン取得CLI

このリポジトリには、X API の OAuth 2.0 Authorization Code Flow with PKCE を使って
ユーザーアクセストークンを取得する CLI を用意しました。

## 数週間以上の連続運用について（refresh token 推奨）

X の OAuth2 アクセストークンは運用上 **短時間で失効**する前提です。
数週間にわたって自動実行したい場合は、`offline.access` スコープを付けて **refresh token を発行**し、
失効時に自動で更新できるようにしておくのが安全です。

このリポジトリの実装では、取得したトークンを `state/x_oauth_tokens.json` に保存し、
ブックマーク取得で 401 が出た場合に **1回だけ refresh → リトライ**するようにしています。

## 事前準備

1. X Developer Console でアプリを作成し、OAuth 2.0 を有効化します。
2. Callback URL（Redirect URI）を登録します（完全一致が必要）。
3. `Client ID`（confidential client の場合は `Client Secret`）を控えます。
4. 必要なスコープを決めます。
   - ブックマーク取得に必要なスコープ: `bookmark.read`, `tweet.read`, `users.read`

## 使い方

### 1) 環境変数を設定（推奨）

`.env` に以下を設定すると、CLI 引数を省略できます。

```
X_CLIENT_ID=...
X_CLIENT_SECRET=...        # public client の場合は空でOK
X_REDIRECT_URI=...         # Console に登録した Callback URL
```

### 2) CLI でアクセストークンを取得

```
uv run daily-research-agent x-auth \
  --scopes "users.read tweet.read bookmark.read offline.access"
```

- ブラウザが開くので、認可画面で許可します。
- リダイレクト後の URL を CLI に貼り付けると、トークン交換が行われます。
- 成功すると JSON で `access_token` / `refresh_token` が表示され、`state/x_oauth_tokens.json` に保存されます。
- `.env` には最低限 `X_USER_ACCESS_TOKEN` を設定してください（下の例が表示されます）。

### 2.1) refresh token の更新だけしたい場合

```
uv run daily-research-agent x-refresh
```

`state/x_oauth_tokens.json`（または `.env` の `X_REFRESH_TOKEN`）から refresh token を読み、
新しい `access_token` を取得してトークンファイルを更新します。

### 3) CLI 引数で指定したい場合

```
uv run daily-research-agent x-auth \
  --client-id "YOUR_CLIENT_ID" \
  --client-secret "YOUR_CLIENT_SECRET" \
  --redirect-uri "https://your-callback.example" \
  --scopes "users.read tweet.read bookmark.read offline.access"
```

## 注意点

- `offline.access` を付けない場合、アクセストークンの有効期限は短めです（デフォルト2時間）。
- `offline.access` を付けると refresh token が発行されます。
- 認可コード（`code`）は有効期限が非常に短いので、URL を貼り付けたらすぐに交換してください。
- `Client Secret` や `access_token` は **必ず .env に保存**し、Git にコミットしないでください。
  - 本リポジトリではトークンファイルを `state/` 配下に保存します（`.gitignore` 対象）。

## daily-research-agent 側で必要な設定

```
X_USER_ACCESS_TOKEN=...  # 取得した access_token
X_REFRESH_TOKEN=...      # refresh token（offline.access を付けた場合）
X_API_BASE_URL=https://api.x.com
```

`configs/agent.toml` の `x.enabled` が `true` なら、次回実行時にブックマーク取得が有効になります。

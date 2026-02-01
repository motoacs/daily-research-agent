from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
import sys

import typer
from dotenv import load_dotenv

from daily_research_agent.config import ConfigError, load_config, resolve_preset
from daily_research_agent.orchestrator import OrchestratorError, run_orchestrator
from daily_research_agent.tools.x_oauth import (
    XOAuthError,
    build_authorize_url,
    exchange_code_for_token,
    generate_oauth_state,
    load_token_payload,
    parse_redirect_url,
    refresh_access_token,
    resolve_env,
    save_token_payload,
    token_file_path,
)

app = typer.Typer(add_completion=False)


@app.command("run")
def run(
    preset: str = typer.Option(..., "--preset", help="Preset name from configs/agent.toml"),
    run_date: str = typer.Option(
        None, "--date", help="Article date in YYYY-MM-DD (defaults to today)"
    ),
    config_path: Path = typer.Option(
        Path("./configs/agent.toml"), "--config", help="Path to agent config TOML"
    ),
) -> None:
    load_dotenv()
    try:
        config = load_config(config_path)
        if run_date:
            article_date = datetime.strptime(run_date, "%Y-%m-%d").date()
        else:
            article_date = date.today()
        preset_loaded = resolve_preset(config, preset, article_date)
        asyncio.run(run_orchestrator(config, preset_loaded, article_date))
    except (ConfigError, OrchestratorError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command("x-auth")
def x_auth(
    client_id: str = typer.Option(None, "--client-id", help="X OAuth2 client ID"),
    client_secret: str = typer.Option(None, "--client-secret", help="X OAuth2 client secret"),
    redirect_uri: str = typer.Option(None, "--redirect-uri", help="Redirect URI"),
    scopes: str = typer.Option(
        "users.read tweet.read bookmark.read offline.access",
        "--scopes",
        help="Space or comma separated scopes",
    ),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
    redirect_url: str = typer.Option(
        None,
        "--redirect-url",
        help="Paste full redirect URL to skip interactive prompt",
    ),
    state_dir: Path = typer.Option(
        Path("./state"),
        "--state-dir",
        help="Directory to store token cache (gitignored)",
    ),
) -> None:
    load_dotenv()
    client_id = resolve_env("X_CLIENT_ID", client_id)
    client_secret = resolve_env("X_CLIENT_SECRET", client_secret)
    redirect_uri = resolve_env("X_REDIRECT_URI", redirect_uri)

    if not client_id or not redirect_uri:
        typer.echo("Error: --client-id and --redirect-uri are required (or env vars).", err=True)
        raise typer.Exit(code=1)

    scope_list = [s for s in scopes.replace(",", " ").split() if s]
    oauth_state = generate_oauth_state()
    auth_url = build_authorize_url(client_id, redirect_uri, scope_list, oauth_state)

    typer.echo("Open the following URL in your browser and authorize the app:")
    typer.echo(auth_url)

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(auth_url)
        except Exception:
            typer.echo("Warning: failed to open browser automatically.")

    if not redirect_url:
        redirect_url = typer.prompt("Paste the full redirect URL")

    params = parse_redirect_url(redirect_url)
    code = params.get("code")
    state = params.get("state")
    if not code:
        typer.echo("Error: 'code' not found in redirect URL.", err=True)
        raise typer.Exit(code=1)
    if state and state != oauth_state.state:
        typer.echo("Error: state mismatch. Aborting.", err=True)
        raise typer.Exit(code=1)

    try:
        token_payload = exchange_code_for_token(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code=code,
            code_verifier=oauth_state.code_verifier,
        )
    except XOAuthError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Persist tokens under state/ so the agent can refresh without editing .env.
    token_path = token_file_path(state_dir)
    save_token_payload(token_path, token_payload)

    typer.echo("Token response (saved to token file):")
    typer.echo(str(token_path))
    typer.echo(json.dumps(token_payload, ensure_ascii=False, indent=2))
    typer.echo("")
    typer.echo("Add these to .env (at minimum):")
    if token_payload.get("access_token"):
        typer.echo(f"X_USER_ACCESS_TOKEN={token_payload['access_token']}")
    if token_payload.get("refresh_token"):
        typer.echo(f"X_REFRESH_TOKEN={token_payload['refresh_token']}")


@app.command("x-refresh")
def x_refresh(
    client_id: str = typer.Option(None, "--client-id", help="X OAuth2 client ID"),
    client_secret: str = typer.Option(None, "--client-secret", help="X OAuth2 client secret"),
    refresh_token: str = typer.Option(None, "--refresh-token", help="Refresh token"),
    state_dir: Path = typer.Option(
        Path("./state"),
        "--state-dir",
        help="Directory where the token file is stored (gitignored)",
    ),
) -> None:
    load_dotenv()
    client_id = resolve_env("X_CLIENT_ID", client_id)
    client_secret = resolve_env("X_CLIENT_SECRET", client_secret)

    token_path = token_file_path(state_dir)
    cached = load_token_payload(token_path) or {}
    refresh_token = resolve_env("X_REFRESH_TOKEN", refresh_token) or cached.get("refresh_token")

    if not client_id:
        typer.echo("Error: --client-id is required (or env var X_CLIENT_ID).", err=True)
        raise typer.Exit(code=1)
    if not refresh_token:
        typer.echo(
            "Error: refresh token not found. Provide --refresh-token, set X_REFRESH_TOKEN, or run x-auth.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        token_payload = refresh_access_token(
            client_id=client_id, refresh_token=refresh_token, client_secret=client_secret
        )
    except XOAuthError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    save_token_payload(token_path, token_payload)
    typer.echo("Refreshed token payload (saved to token file):")
    typer.echo(str(token_path))
    typer.echo(json.dumps(token_payload, ensure_ascii=False, indent=2))

def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())

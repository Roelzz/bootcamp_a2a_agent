# A2A Server Bootcamp

Generic conversational agent exposed over [A2A](https://a2a-protocol.org) (protocol v0.3.0, JSON-RPC over HTTP) so a Copilot Studio agent can call it as a connected agent. No LLM: it greets, answers `help`, and echoes messages while remembering each conversation by `contextId`.

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/agent-card.json` | Agent card (legacy `/.well-known/agent.json` also served) |
| `POST /` | JSON-RPC: `message/send`, `message/stream` |

## Run locally

```bash
uv sync
cp .env.example .env
uv run main.py        # http://localhost:2009
```

If direct PyPI access is blocked on your machine, point uv at your approved mirror: `UV_DEFAULT_INDEX=<mirror simple URL> uv sync`.

Set `LOG_LEVEL=DEBUG` to log the raw request body and headers of every call (shows the exact wire format a client sends).

## Test

```bash
uv run pytest                                      # local, in-process
A2A_E2E_URL=https://<fqdn> uv run pytest -m e2e    # same checks against a deployed server
```

## Deploy to Azure Container Apps

```bash
az login
AZURE_SUBSCRIPTION_ID=<id> ./deploy.sh
```

Creates (or updates) the resource group, container registry, environment and container app from the `Dockerfile` (cloud build, no local Docker needed) and prints the agent card URL. Settings live in `.env`, see `.env.example`. The card URL is derived from the Container Apps environment, so no `PUBLIC_BASE_URL` is needed.

The image installs hash-pinned dependencies exported from `uv.lock`, so it builds against the default package index even if the lockfile was resolved through a mirror.

Logs: `az containerapp logs show -n a2a-server-bootcamp -g rg-a2a-server-bootcamp --follow`

## Connect from Copilot Studio

1. Open your agent, then **Build** → **Connected agents** → **Add** → **Add A2A agent**.
2. Agent card URL: `https://<fqdn>/.well-known/agent-card.json` (name and description fill in automatically).
3. Authentication: **None**, then **Create connection** and **Add**.
4. Test in the Preview pane: "Hello", "What can you do?", then a few follow-up messages.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `2009` | Listen port |
| `LOG_LEVEL` | `INFO` | Log level |
| `AGENT_NAME` | `Bootcamp Echo Agent` | Name on the agent card |
| `AGENT_DESCRIPTION` | built in | Card description, 30-1000 characters (Copilot Studio requirement) |
| `PUBLIC_BASE_URL` | auto | URL advertised in the card; auto-detected on Container Apps, else `http://localhost:$PORT/` |

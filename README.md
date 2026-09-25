# cloudHPC MCP server

Run engineering simulations on [cloudHPC](https://cloudhpc.cloud) from AI assistants
that support the Model Context Protocol (MCP): Claude, ChatGPT/Codex, Gemini,
GitHub Copilot and others, from their desktop apps, terminal apps and, where
supported, web apps. Inspect a local case, get vCPU/RAM advice, upload the
folder, launch, monitor, diagnose errors and download the results, all from a
conversation.

Supported solvers: everything available on cloudHPC (FDS, OpenFOAM,
snappyHexMesh, code_aster, CalculiX, OpenRadioss, SU2, ...). Resource advice
follows the [cloudHPC scalability rules](https://docs.cloudhpc.cloud/scalability/)
and run diagnosis follows the [cloudHPC errors guide](https://docs.cloudhpc.cloud/errors/).

- [What you can ask](#what-you-can-ask)
- [Tools](#tools)
- [Installation](#installation)
- [Connect your AI assistant](#connect-your-ai-assistant)
  - [Claude](#claude) · [ChatGPT / Codex](#chatgpt--codex) · [Gemini](#gemini) · [GitHub Copilot](#github-copilot) · [Other clients](#other-mcp-clients)
  - [Web apps (hosted endpoint)](#web-apps-hosted-endpoint)
- [Good to know](#good-to-know)
- [Troubleshooting](#troubleshooting)

## What you can ask

- *"Check the FDS case in this folder and tell me which resources to use."*
- *"Run it on cloudHPC and download the results when it finishes."*
- *"What is the status of my running simulations?"*
- *"My last run failed: what went wrong?"*

## Tools

| Tool | What it does |
|---|---|
| `inspect_case` ¹ | Detect solver and model size of a local folder (FDS meshes/MPI groups, OpenFOAM cells, CalculiX nodes, ...) and run pre-flight checks |
| `suggest_resources` | vCPU and RAM type recommendation, with reasoning |
| `list_solvers`, `list_machine_options` | Available solvers, vCPU counts and RAM types |
| `upload_folder` ¹ | Compress a local case folder and upload it to your storage |
| `launch_simulation` | Launch a run (**asks for confirmation**) |
| `list_simulations`, `get_simulation`, `wait_for_simulation` | Follow your runs; finished runs include a diagnosis of known errors |
| `sync_simulation` | Upload partial results of a running job |
| `stop_simulation` | Soft or hard stop (**asks for confirmation**) |
| `open_remote_desktop` | Browser remote-desktop link of a running job |
| `list_storage`, `list_results` | Browse your storage and result archives |
| `download_results` ¹ | Download result archives and extract them locally |
| `get_upload_link`, `get_download_link` | Temporary links to upload/download single files |
| `delete_storage` | Delete a file or folder (**asks for confirmation**) |
| `api_usage` | API rate limits and calls used |

Actions that cost money or delete data (launching a run, a hard stop,
deleting from storage) always need your confirmation. In apps that support it
(e.g. Claude Code) the server shows you a confirmation dialog directly, so the
assistant cannot confirm on your behalf; in other apps the assistant shows you
a summary and waits for your OK.

¹ Only with the local installation: they work on files on your computer. In web
apps (hosted endpoint) use `get_upload_link` / `get_download_link` instead.

## Installation

Requirements:

- Python 3.10 or newer
- A cloudHPC account and its API key: open your cloudHPC profile page
  ([APIKEY docs](https://docs.cloudhpc.cloud/APIKEY/)). The key gives full access
  to your account: keep it private.

Install with [pipx](https://pipx.pypa.io) (recommended: isolated, and the
command ends up in `~/.local/bin`, easy to find for desktop apps):

```bash
pipx install git+https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp
```

or with pip:

```bash
pip install git+https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp
```

This installs the `cloudhpc-mcp` command. Find its full path, you may need it
below:

```bash
which cloudhpc-mcp          # Linux / macOS
where cloudhpc-mcp          # Windows
```

The API key is read from the `CLOUDHPC_APIKEY` environment variable or, if that
is not set, from the file `~/.cfscloudhpc/apikey` created by
[cloudHPCexec](https://github.com/CFD-FEA-SERVICE/CloudHPC/tree/master/exampleAPI).

## Connect your AI assistant

There are two ways to connect:

- **Local installation** (desktop and terminal apps): the server runs on your
  computer, so it can read your case folders and save results next to them. All
  tools are available. Recommended.
- **Hosted endpoint** (web apps): `https://mcp.cloudhpc.cloud/mcp`, nothing to
  install. It cannot read files on your computer: you upload cases and download
  results with temporary links (or from the cloudHPC web app). See
  [Web apps](#web-apps-hosted-endpoint).

| Assistant | Desktop app | Terminal (Linux) | Web app |
|---|---|---|---|
| Claude | [Claude Desktop](#claude-desktop) (Windows, macOS, Linux beta) | [Claude Code](#claude-code) | [claude.ai](#claudeai) (custom connector) |
| ChatGPT | [ChatGPT desktop app](#chatgpt-desktop-app) (where MCP servers are available) | [Codex CLI](#codex-cli) | [not yet](#chatgpt-web) |
| Gemini | not supported: the Gemini desktop app has no MCP support | [Gemini CLI](#gemini-cli) | not supported |
| GitHub Copilot | [VS Code (Copilot agent mode)](#vs-code-copilot-agent-mode) | [Copilot CLI](#copilot-cli) | not supported |

In every example replace `your-api-key` with your key. If the assistant cannot
find the `cloudhpc-mcp` command, write its full path instead (see
[Installation](#installation)).

### Claude

#### Claude Desktop

Open **Settings > Developer > Edit Config** and add the server to
`claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: use **Edit Config** to open the file

```json
{
  "mcpServers": {
    "cloudhpc": {
      "command": "cloudhpc-mcp",
      "env": { "CLOUDHPC_APIKEY": "your-api-key" }
    }
  }
}
```

Restart Claude Desktop. The cloudHPC tools appear in the tools menu of a new
conversation.

#### Claude Code

```bash
claude mcp add --scope user cloudhpc -e CLOUDHPC_APIKEY=your-api-key -- cloudhpc-mcp
```

`--scope user` makes it available in every folder. Check it with `claude mcp list`
or `/mcp` inside Claude Code.

### ChatGPT / Codex

#### ChatGPT desktop app

In recent versions of the ChatGPT desktop app: **Settings > MCP servers > Add
server**, choose **STDIO**:

- Command: `cloudhpc-mcp`
- Environment variable: `CLOUDHPC_APIKEY` = `your-api-key`

The app shares its MCP configuration with Codex (`~/.codex/config.toml`, see
below), so a server added in one is available in the other. Availability
depends on app version and plan. The ChatGPT **web** app only supports remote
connectors and cannot run this local server.

#### Codex CLI

```bash
codex mcp add cloudhpc --env CLOUDHPC_APIKEY=your-api-key -- cloudhpc-mcp
```

or edit `~/.codex/config.toml`:

```toml
[mcp_servers.cloudhpc]
command = "cloudhpc-mcp"
tool_timeout_sec = 1900        # wait_for_simulation can wait up to 30 minutes

[mcp_servers.cloudhpc.env]
CLOUDHPC_APIKEY = "your-api-key"
```

Check it with `codex mcp list`.

### Gemini

#### Gemini CLI

```bash
gemini mcp add -s user -e CLOUDHPC_APIKEY=your-api-key cloudhpc cloudhpc-mcp
```

or edit `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "cloudhpc": {
      "command": "cloudhpc-mcp",
      "env": { "CLOUDHPC_APIKEY": "$CLOUDHPC_APIKEY" },
      "timeout": 1900000
    }
  }
}
```

`$CLOUDHPC_APIKEY` takes the key from your shell (`export CLOUDHPC_APIKEY=...`);
`timeout` (milliseconds) lets `wait_for_simulation` wait up to 30 minutes.
Check it with `/mcp` inside Gemini CLI.

The Gemini desktop and web apps do not support custom MCP servers.

### GitHub Copilot

#### VS Code (Copilot agent mode)

Run **MCP: Open User Configuration** from the Command Palette (or create
`.vscode/mcp.json` in a project) and add:

```json
{
  "inputs": [
    { "type": "promptString", "id": "cloudhpc-key",
      "description": "cloudHPC API key", "password": true }
  ],
  "servers": {
    "cloudhpc": {
      "type": "stdio",
      "command": "cloudhpc-mcp",
      "env": { "CLOUDHPC_APIKEY": "${input:cloudhpc-key}" }
    }
  }
}
```

VS Code asks for the key once and stores it securely. Open Copilot Chat in
**Agent** mode and enable the cloudHPC tools from the tools picker.

#### Copilot CLI

Edit `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "cloudhpc": {
      "type": "local",
      "command": "cloudhpc-mcp",
      "args": [],
      "env": { "CLOUDHPC_APIKEY": "your-api-key" },
      "tools": ["*"]
    }
  }
}
```

or use `/mcp add` inside a Copilot CLI session. The Microsoft Copilot app for
Windows does not support custom MCP servers.

### Other MCP clients

Configure a **stdio** server with command `cloudhpc-mcp` and the environment
variable `CLOUDHPC_APIKEY`.

### Web apps (hosted endpoint)

Endpoint: `https://mcp.cloudhpc.cloud/mcp` (streamable HTTP). Every request must
carry your cloudHPC API key in the `X-API-Key` header (or
`Authorization: Bearer <key>`). The endpoint stores nothing: the key is only
forwarded to the cloudHPC API for that request.

What changes compared with the local installation:

- `inspect_case`, `upload_folder` and `download_results` are not available: the
  hosted server cannot see your computer.
- To upload a case, compress the **content** of the case folder (files at the
  root of the archive) and ask the assistant for an upload link
  (`get_upload_link`): it gives you a ready `curl` command. Or upload it from
  the cloudHPC web app.
- To get results, ask for a download link (`get_download_link`).

#### claude.ai

On plans with custom connectors: **Settings > Connectors > Add custom
connector**.

- URL: `https://mcp.cloudhpc.cloud/mcp`
- Authentication: **No sign-in**, then under **Request headers** add
  `X-API-Key` = `your-api-key`

Enable the connector in a conversation from the tools menu. Request-header
authentication is being rolled out gradually: if your account only offers
OAuth, use Claude Desktop or Claude Code with the local installation. The same
connector is also available in the Claude desktop and mobile apps.

#### ChatGPT web

ChatGPT web connectors (developer mode) currently accept only OAuth or no
authentication, not an API-key header, so they cannot connect to this
endpoint yet. Use the [ChatGPT desktop app](#chatgpt-desktop-app) or
[Codex CLI](#codex-cli) with the local installation.

#### Gemini and Copilot web

The Gemini web app, Microsoft Copilot and Copilot Chat on github.com do not
support custom MCP servers.

#### Other clients using the hosted endpoint

Any MCP client that supports streamable HTTP with custom headers works, for
example:

```bash
claude mcp add --transport http cloudhpc https://mcp.cloudhpc.cloud/mcp \
  --header "X-API-Key: your-api-key"
```

```json
{ "mcpServers": { "cloudhpc": {
    "httpUrl": "https://mcp.cloudhpc.cloud/mcp",
    "headers": { "X-API-Key": "your-api-key" } } } }
```

(the second is the Gemini CLI format; VS Code uses `"type": "http"`, `"url"`
and `"headers"`; Codex CLI uses `url` and `http_headers` in `config.toml`).

## Good to know

- **Upload layout**: the content of the case folder is archived at the root of
  `upload.tar.gz` and uploaded into a storage folder with the same name as the
  local folder. Hidden files are skipped. Folder names must not contain
  `, ( ) ' $ ~ " #` or spaces.
- **Resources**: FDS, CalculiX and code_aster start on `highcpu`; after a memory
  error move to `standard`, then `highmem`. OpenFOAM and other MPI-only solvers
  use `highcore` or `hypercore`. 1 vCPU on `highcpu` is never suggested for a
  solver: it has too little RAM to start.
- **Checking runs**: a run can end as COMPLETED even if the solver failed. When
  a run ends the server scans its output for the errors listed in the
  [errors guide](https://docs.cloudhpc.cloud/errors/) and suggests the fix;
  after downloading, it also checks the solver's own logs (OpenFOAM `log.*`,
  FDS `.out`).
- **Costs** are billed per vCPU-hour and shown in euro when a run ends.
- **Storage**: files are deleted automatically 60 days after creation. Download
  your results.
- **Rate limits**: 100 API calls/hour on free accounts, 500 on full accounts (no daily limit).
  The server reads the rate-limit headers and stops before exceeding them.

## Troubleshooting

| Problem | Fix |
|---|---|
| The assistant does not see the cloudHPC tools | Restart the app after editing its configuration; in terminal apps check with `/mcp` or `mcp list`. |
| `command not found` / server fails to start | Use the full path of `cloudhpc-mcp` (`which cloudhpc-mcp`). Desktop apps do not load your shell's PATH, conda or virtual environments. |
| `Unauthorized: the API key is invalid` | Copy the key again from your cloudHPC profile page. |
| Hosted endpoint: `Invalid header name` | Write the header exactly as `X-API-Key: your-api-key` (name, colon, space, key). |
| `rate limit reached` | Wait for the next hour, or ask the assistant to check less often. |
| The wait for a run is cut off | Raise the tool timeout of your client (see the examples above) or ask the assistant to wait in shorter steps. |

## Development

```bash
git clone https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp
cd cloudhpc-mcp
pip install -e ".[test]"
pytest            # offline tests with a mocked API
```

`scripts/e2e_test.py` runs the whole workflow against the real API with your
account: it uploads a tiny FDS case and, with `--confirm-costs`, runs it on
1 vCPU for about a minute (`--no-launch` only uploads; `--cleanup` deletes the
test folder at the end).

## Support

- Documentation: https://docs.cloudhpc.cloud
- Issues: https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp/issues

## License

Apache-2.0

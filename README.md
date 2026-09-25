# cloudHPC MCP server

Run engineering simulations on [cloudHPC](https://cloudhpc.cloud) from AI assistants
that support the Model Context Protocol (Claude Desktop, Claude Code, Gemini CLI,
Cursor, ...): inspect a local case, get vCPU/RAM advice, upload the folder,
launch, monitor, diagnose errors and download the results.

Supported solvers: everything available on cloudHPC (FDS, OpenFOAM,
snappyHexMesh, code_aster, CalculiX, OpenRadioss, SU2, ...). Resource advice
follows the [cloudHPC scalability rules](https://docs.cloudhpc.cloud/scalability/)
and run diagnosis follows the [cloudHPC errors guide](https://docs.cloudhpc.cloud/errors/).

## What you can ask

- *"Check the FDS case in this folder and tell me which resources to use."*
- *"Run it on cloudHPC and download the results when it finishes."*
- *"What is the status of my running simulations?"*
- *"My last run failed: what went wrong?"*

## Tools

| Tool | What it does |
|---|---|
| `inspect_case` | Detect solver and model size of a local folder (FDS meshes/MPI groups, OpenFOAM cells, CalculiX nodes, ...) and run pre-flight checks |
| `suggest_resources` | vCPU and RAM type recommendation, with reasoning |
| `list_solvers`, `list_machine_options` | Available solvers, vCPU counts and RAM types |
| `upload_folder` | Compress a local case folder and upload it to your storage |
| `launch_simulation` | Launch a run (**asks for confirmation**) |
| `list_simulations`, `get_simulation`, `wait_for_simulation` | Follow your runs; finished runs include a diagnosis of known errors |
| `sync_simulation` | Upload partial results of a running job |
| `stop_simulation` | Soft or hard stop (**asks for confirmation**) |
| `open_remote_desktop` | Browser remote-desktop link of a running job |
| `list_storage`, `list_results` | Browse your storage and result archives |
| `download_results` | Download result archives and extract them locally |
| `get_upload_link`, `get_download_link` | Temporary links to upload/download single files |
| `delete_storage` | Delete a file or folder (**asks for confirmation**) |
| `api_usage` | API rate limits and calls used |

Actions that cost money or delete data first return a summary and run only
after you confirm.

## Requirements

- Python 3.10 or newer
- A cloudHPC account and its API key: open your cloudHPC profile page
  ([APIKEY docs](https://docs.cloudhpc.cloud/APIKEY/)). The key gives full access
  to your account: keep it private.

## Installation

```bash
pip install git+https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp
```

This installs the `cloudhpc-mcp` command. The API key is read from the
`CLOUDHPC_APIKEY` environment variable or, if that is not set, from the file
`~/.cfscloudhpc/apikey` created by
[cloudHPCexec](https://github.com/CFD-FEA-SERVICE/CloudHPC/tree/master/exampleAPI).

### Claude Desktop

Add to `claude_desktop_config.json` (Settings > Developer > Edit Config):

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

### Claude Code

```bash
claude mcp add cloudhpc -e CLOUDHPC_APIKEY=your-api-key -- cloudhpc-mcp
```

### Gemini CLI

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "cloudhpc": {
      "command": "cloudhpc-mcp",
      "env": { "CLOUDHPC_APIKEY": "${CLOUDHPC_APIKEY}" },
      "timeout": 1900000
    }
  }
}
```

(`timeout` is raised because `wait_for_simulation` can wait up to 30 minutes.)

### Other MCP clients

Configure a **stdio** server with command `cloudhpc-mcp` and the environment
variable `CLOUDHPC_APIKEY`.

## Good to know

- **Upload layout**: the content of the case folder is archived at the root of
  `upload.tar.gz` and uploaded into a storage folder with the same name as the
  local folder. Hidden files are skipped. Folder names must not contain
  `, ( ) ' $ ~ " #` or spaces.
- **Resources**: FDS, CalculiX and code_aster start on `highcpu`; after a memory
  error move to `standard`, then `highmem`. OpenFOAM and other MPI-only solvers
  use `highcore` or `hypercore`. 1 vCPU on `highcpu` is never suggested for a
  solver: it has too little RAM to start.
- **Storage**: files are deleted automatically 60 days after creation. Download
  your results.
- **Rate limits**: 100 API calls/hour on free accounts, 500 on full accounts.
  The server reads the rate-limit headers and stops before exceeding them.

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

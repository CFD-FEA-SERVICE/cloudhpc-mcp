"""cloudHPC MCP server.

Two modes, same code:
  local  (stdio)            runs on the user's PC; can zip/upload local folders
                            and download/extract results. API key from
                            CLOUDHPC_APIKEY or ~/.cfscloudhpc/apikey.
  remote (streamable-http)  runs on a server; stateless; the
                            API key comes with each request in the X-API-Key
                            or Authorization: Bearer header, or from an OAuth
                            sign-in (oauth.py, when CLOUDHPC_OAUTH_SECRET is
                            set). File transfers are done by the client with
                            signed URLs.
  public (streamable-http)  key-less guide endpoint for website chat bots:
                            only solver_guide, list_solvers,
                            list_machine_options and suggest_resources. The
                            solver/machine catalog is read with a dedicated
                            account key (CLOUDHPC_CATALOG_APIKEY) and cached;
                            requests are limited per client IP.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, advisor, errors, files, guides, live
from .client import ACTIVE_STATUSES, REQUEST_LOG, STARTED_AT, STATUS, CloudHPCClient, CloudHPCError

MODE = os.environ.get("CLOUDHPC_MCP_MODE", "local").lower()  # local | remote | public
if MODE not in ("local", "remote", "public"):
    raise SystemExit(f"CLOUDHPC_MCP_MODE must be local, remote or public, not '{MODE}'")
LOCAL = MODE == "local"
PUBLIC = MODE == "public"

# public mode: the only tools exposed (no account data, no costs)
PUBLIC_TOOLS = ("solver_guide", "list_solvers", "list_machine_options", "suggest_resources")
CATALOG_TTL = int(os.environ.get("CLOUDHPC_CATALOG_TTL", "3600"))          # seconds
PUBLIC_RATE_PER_MIN = int(os.environ.get("CLOUDHPC_PUBLIC_RATE_PER_MIN", "60"))

INSTRUCTIONS = """\
cloudHPC runs engineering simulations (FDS, OpenFOAM, code_aster, CalculiX,
OpenRadioss, SU2, ...) on cloud machines. Typical workflow:

1. Understand the case. In local mode call inspect_case on the user's folder:
   it detects the solver and the model size (FDS meshes, OpenFOAM cells, ...).
   solver_guide is the authoritative description of how cloudHPC runs each
   solver (which input files are picked, what is done automatically, flags,
   parallelism, outputs): ALWAYS call it before answering questions on how to
   prepare or run a case for a solver, instead of guessing.
2. Choose the solver version with list_solvers and the resources with
   suggest_resources. Explain the suggestion briefly (vCPU, RAM type, why).
   RAM types: highcpu < standard < highmem use the same CPUs with 1 to 8 GB
   per vCPU and hyperthreading; highcore/hypercore use physical cores only
   (hypercore = faster CPU generation). Physical cores = vCPU on
   highcore/hypercore but vCPU / 2 on highcpu/standard/highmem/hypercpu.
   MPI-only solvers (OpenFOAM & co.) run one process per physical core, so
   32 vCPU on standard give 16 processes (numberOfSubdomains set to 16). OpenFOAM and other MPI-only solvers
   must use highcore or hypercore. For FDS/CalculiX/code_aster start on
   highcpu and move to standard, then highmem, only after a memory error.
3. Upload: local mode -> upload_folder (tar.gz of the folder content into a
   storage folder with the same name). Remote mode -> get_upload_link and
   give the user the curl command.
4. Launch with launch_simulation. It costs money: always show the user the
   summary returned by the first call and only then call again with
   confirm=true.
5. Monitor with get_simulation / wait_for_simulation. Status codes:
   10 COMPLETED, 20 PENDING, 30 RUNNING, 40 STOPPING, 50 STOPPED, 60 ERROR.
   sync_simulation uploads partial results of a running job.
   When a run ends, check the diagnosis (wait_for_simulation adds it, or
   get_simulation(include_log=true)): a run can be COMPLETED even if the
   solver failed. Each diagnosis item has cause, fix and docs link. When it
   reports memory_error, relaunch on the suggested RAM tier. Never use
   1 vCPU on highcpu for engineering solvers (not enough RAM to start).
   The solver's own log (e.g. <CHID>.log for FDS) is also in the storage
   folder and in the downloaded results.
6. Results are archives in the same storage folder (FDS.tar.gz,
   OPENFOAM-solution.tar.gz, CALCULIX.tar.gz, ...). list_results then
   download_results (local) or get_download_link (remote).

To resume a stopped run use resume_simulation (FDS and OpenFOAM: same solver,
vCPU, RAM type and folder). For post-processing on a remote desktop use
start_desktop and always remind the user to stop it: it is billed until stopped.
To change a RUNNING simulation without stopping it (OpenFOAM controlDict,
fvSolution, fvSchemes; FDS .stop; code_saturne control_file; OpenRadioss .ctl)
read the current file with live_settings, then use update_running_simulation.
If a tool is missing, the user may have signed in with read-only access.

Never modify, move or delete the user's case files on your own initiative:
report what the pre-flight checks found, propose the fix and ask first.

Storage files are deleted automatically after 60 days: remind the user to
download results. If the storage folder or the result files of a simulation are
missing, tell the user they were most likely deleted automatically after 60
days or deleted manually from the storage; do not speculate further. Avoid needless calls: the API is rate limited
(100 calls/hour on free accounts, 500 on full accounts; no daily limit).
Simulation costs are in euro and known only after the run.
"""

PUBLIC_INSTRUCTIONS = """\
cloudHPC runs engineering simulations (FDS, OpenFOAM, code_aster, CalculiX,
OpenRadioss, SU2, EnergyPlus, ...) on cloud machines. This endpoint answers
general "how do I..." questions; it has no access to any user account.

- solver_guide is the authoritative description of how cloudHPC runs each
  solver (input files, what is done automatically, parallelism, resources,
  logs): ALWAYS call it before answering how to prepare or run a case,
  instead of guessing.
- list_solvers: available solvers and versions. list_machine_options: vCPU
  and RAM types. suggest_resources: vCPU and RAM type for a model size.
  RAM types: highcpu < standard < highmem use the same CPUs with 1 to 8 GB
  per vCPU and hyperthreading; highcore/hypercore use physical cores only.
  Physical cores = vCPU on highcore/hypercore, vCPU / 2 on the others.
  OpenFOAM and other MPI-only solvers run one process per physical core: if
  a user reports fewer processes or subdomains than vCPU selected (e.g. 16
  with 32 vCPU), explain this first and suggest highcore or hypercore.
  OpenFOAM and other MPI-only solvers must use highcore or hypercore. For
  FDS/CalculiX/code_aster start on highcpu and move to standard, then
  highmem, only after a memory error.

Never ask for API keys, passwords, simulation IDs or files, and never accept
them in the chat. For anything about the user's own account (their
simulations, storage, results, costs, errors of a specific run) point them to
support, or to connecting their own AI assistant to cloudHPC with their API
key: https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp . Documentation:
https://docs.cloudhpc.cloud . Costs are in euro.
"""

mcp = MCPServer("cloudHPC", instructions=PUBLIC_INSTRUCTIONS if PUBLIC else INSTRUCTIONS, version=__version__, log_level="WARNING")

# never log request URLs: signed upload/download links carry access tokens
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

HOST = os.environ.get("HOST", "0.0.0.0" if not LOCAL else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


# ------------------------------------------------------------------ helpers

def _local_api_key() -> str:
    key = os.environ.get("CLOUDHPC_APIKEY", "").strip()
    if key:
        return key
    keyfile = Path.home() / ".cfscloudhpc" / "apikey"  # shared with cloudHPCexec
    try:
        return keyfile.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _remote_api_key(ctx: Context | None) -> str:
    try:
        headers = ctx.headers if ctx else None
    except (ValueError, AttributeError):
        headers = None
    if not headers:
        return ""
    key = headers.get("x-api-key", "")
    if not key:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:]
    return key.strip()


def client_for(ctx: Context | None) -> CloudHPCClient:
    if PUBLIC:  # dedicated catalog account; only catalog calls are made with it
        key = os.environ.get("CLOUDHPC_CATALOG_APIKEY", "").strip()
    else:
        key = _local_api_key() if LOCAL else _remote_api_key(ctx)
    return CloudHPCClient(api_key=key)


# public mode: solver list and machine options, shared by all visitors
_CATALOG: dict[str, tuple[float, Any]] = {}


async def _catalog(ctx: Context | None, what: Literal["scripts", "cpu", "ram"]) -> Any:
    """Solver list / vCPU options / RAM types. Cached for CATALOG_TTL in public
    mode (about one API call per hour whatever the traffic); if the API fails,
    the last good value is served."""
    hit = _CATALOG.get(what) if PUBLIC else None
    if hit and time.time() - hit[0] < CATALOG_TTL:
        return hit[1]
    c = client_for(ctx)
    try:
        value = await {"scripts": c.scripts, "cpu": c.cpu_options, "ram": c.ram_options}[what]()
    except CloudHPCError:
        if hit:
            return hit[1]
        raise
    if PUBLIC:
        _CATALOG[what] = (time.time(), value)
    return value


def _sim_summary(s: dict) -> dict:
    status = s.get("status")
    return {
        "id": s.get("id"),
        "status": STATUS.get(status, str(status)),
        "status_code": status,
        "solver": s.get("script"),
        "cpu": s.get("cpu"),
        "ram": s.get("ram"),
        "regular_instance": bool(s.get("nopre")),
        "folder": s.get("folder"),
        "mesh_folder": s.get("mesh") or None,
        "submitted": s.get("idate") or None,
        "ended": s.get("edate") or None,
        "cpu_hours": s.get("cpu_hrs") or None,
        "cost_eur": _eur(s.get("cost")),
    }


# storage folder -> case info from the last upload in this process (local mode),
# used by launch_simulation to check vCPU against the case (e.g. FDS meshes).
UPLOADED_CASES: dict[str, dict] = {}


def _fds_core_problem(info: dict | None, cpu: int, ram: str) -> tuple[str | None, str | None]:
    """(blocking problem, warning) for an FDS case: one MPI process per mesh or
    MPI_PROCESS group. Fewer vCPU than groups stops the run ("low vCPU
    selected"); fewer physical cores than groups runs, but slowly."""
    fds = (info or {}).get("fds")
    if not fds or advisor.fds_auto_split(fds):
        return None, None  # single mesh: cloudHPC splits it to fit the cores
    groups = fds.get("mpi_groups") or fds["meshes"]
    if cpu < groups:
        need = groups if ram in advisor.PHYSICAL_RAM else groups * 2
        return (f"The case has {groups} mesh/MPI group(s) but only {cpu} vCPU: the run would "
                f"stop with 'low vCPU selected'. Use at least {groups} vCPU ({need} for best "
                "speed), or group meshes with MPI_PROCESS."), None
    cores = cpu if ram in advisor.PHYSICAL_RAM else max(cpu // 2, 1)
    if cores < groups:
        return None, (f"{groups} MPI processes on {cores} physical core(s) ({cpu} vCPU {ram}): it "
                      f"runs, but two processes share each core. {groups * 2} vCPU on {ram} "
                      f"(or {groups} on highcore/hypercore) would be faster.")
    return None, None


def _eur(value: Any) -> str | None:
    """cloudHPC costs are always in euro."""
    try:
        return f"€{float(value):.2f}" if float(value) >= 1 else f"€{float(value):.3f}"
    except (TypeError, ValueError):
        return None


def _storage_item(i: dict) -> dict:
    out = {"name": i.get("basename"), "path": i.get("name"), "type": i.get("type")}
    if i.get("type") == "file":
        size = i.get("size")
        out["size_mb"] = round(int(size) / 1e6, 2) if str(size).isdigit() else None
        out["created"] = i.get("timeCreated") or None
    return out


def _fds_progress(text: str) -> dict | None:
    """Simulated time vs T_END from the FDS output (the run log prints both)."""
    tend = re.search(r"T_END\s*=\s*([0-9.eEdD+-]+)", text)
    times = re.findall(r"Simulation Time:\s*([0-9.eE+-]+)\s*s", text)
    if not tend or not times:
        return None
    try:
        t_end = float(tend.group(1).replace("D", "E").replace("d", "e"))
        t_now = float(times[-1])
    except ValueError:
        return None
    if t_end <= 0:
        return None
    return {"simulated_time_s": t_now, "t_end_s": t_end,
            "percent": round(min(100.0, 100.0 * t_now / t_end), 1),
            "restarts": len(re.findall(r"=> ADD RESTART|\*\* RESTART TRUE \*\*", text))}


def _diagnosis(s: dict) -> dict:
    """Scan the simulation output/log for known errors (docs.cloudhpc.cloud/errors)."""
    text = "\n".join(str(s.get(k) or "") for k in ("output", "logs"))
    found = errors.diagnose(text)
    out: dict[str, Any] = {}
    if found:
        out["diagnosis"] = found
    if any(f["id"] in ("ram_out", "ram_high") for f in found):
        nxt = advisor.next_ram(s.get("ram", ""))
        out["memory_error"] = any(f["id"] == "ram_out" for f in found)
        out["suggestion"] = (
            f"Relaunch the same case with the same vCPU on '{nxt}'." if nxt else
            "Increase vCPU (RAM grows with vCPU) or reduce the model size.")
    if advisor.family_of(str(s.get("script", ""))) == "fds":
        prog = _fds_progress(text)
        if prog:
            out["fds_progress"] = prog
    if s.get("status") == 10 and any(f["severity"] == "error" for f in found):
        out["warning"] = ("Status is COMPLETED but the output contains errors: the solver "
                          "probably did not finish correctly.")
    # FDS prints a definite success line; check it when the solver log is in the output
    if advisor.family_of(str(s.get("script", ""))) == "fds" and "Starting FDS" in text:
        ok = "FDS completed successfully" in text
        out["solver_finished_ok"] = ok
        if s.get("status") == 10 and not ok:
            out.setdefault("warning", "Status is COMPLETED but FDS did not print 'STOP: FDS "
                                      "completed successfully': check <CHID>.out in the results.")
    return out


class _Confirm(BaseModel):
    confirm: bool = Field(description="Tick to confirm")


async def _ask_user(ctx: Context | None, summary: str) -> str:
    """Ask the USER directly through MCP elicitation.

    Returns "yes", "no", or "unavailable" (client without elicitation, e.g.
    stateless HTTP): then the tool falls back to the confirm=true two-step flow.
    The assistant cannot answer an elicitation on the user's behalf.
    """
    if ctx is None:
        return "unavailable"
    try:
        caps = ctx.client_capabilities
        if not caps or getattr(caps, "elicitation", None) is None:
            return "unavailable"
        res = await ctx.elicit(summary, _Confirm)
    except Exception:
        return "unavailable"
    if res.action == "accept" and getattr(res.data, "confirm", False):
        return "yes"
    return "no"


async def _confirmed(ctx: Context | None, summary: str, confirm: bool) -> dict | None:
    """None = go ahead; otherwise the dict to return to the assistant."""
    answer = await _ask_user(ctx, summary)
    if answer == "yes":
        return None
    if answer == "no":
        return {"cancelled": True, "summary": summary,
                "note": "The user declined in the confirmation dialog. Do not retry."}
    if not confirm:
        return {"confirmation_required": True, "summary": summary,
                "note": "Show this summary to the user and call again with confirm=true "
                        "only after the user explicitly agrees in their next message."}
    return None


MISSING_FILES_HINT = (
    "The files are no longer in the storage. Most likely they were deleted "
    "automatically because they were older than 60 days, or they were deleted "
    "manually from the storage. If the input case is still on the computer, the "
    "simulation can be run again.")


def _is_missing(msg: str) -> bool:
    m = msg.lower()
    return "not found" in m or "technical problem" in m or "no download link" in m


def _err(e: Exception, files_hint: bool = True) -> dict:
    """files_hint: add the 60-day/deleted-files explanation when the API reports a
    missing object. False for calls that do not touch storage or simulations."""
    out: dict[str, Any] = {"error": str(e)}
    if files_hint and _is_missing(str(e)):
        out["hint"] = MISSING_FILES_HINT
    return out


def _catalog_err(e: Exception) -> dict:
    out = _err(e, files_hint=False)
    if PUBLIC:
        out["note"] = ("The solver/machine catalog is temporarily unavailable. solver_guide "
                       "still works; see https://docs.cloudhpc.cloud for the solver list.")
    return out


# ------------------------------------------------------------ discovery

@mcp.tool(annotations=READ)
async def list_solvers(search: str | None = None, ctx: Context = None) -> dict:
    """List the solvers/scripts available on cloudHPC, grouped by family.

    search: optional case-insensitive substring (e.g. "openfoam", "fds6.9").
    """
    try:
        scripts = await _catalog(ctx, "scripts")
    except CloudHPCError as e:
        return _catalog_err(e)
    if search:
        scripts = [s for s in scripts if search.lower() in s.lower()]
    return {"solvers": advisor.group_scripts(scripts)}


@mcp.tool(annotations=READ)
async def list_machine_options(ctx: Context = None) -> dict:
    """List the vCPU counts and RAM/instance types that can be requested."""
    try:
        cpus, rams = await asyncio.gather(_catalog(ctx, "cpu"), _catalog(ctx, "ram"))
    except CloudHPCError as e:
        return _catalog_err(e)
    return {
        "vcpu": cpus,
        "ram_types": rams,
        "ram_guide": {
            "highcpu": "cheapest, ~1 GB/vCPU, hyperthreading",
            "standard": "same CPUs, more RAM per vCPU",
            "highmem": "same CPUs, up to 8 GB/vCPU",
            "highcore": "physical cores only (no hyperthreading) - for MPI solvers like OpenFOAM",
            "hypercpu": "newer/faster CPU generation, hyperthreading",
            "hypercore": "newer/faster CPU generation, physical cores only",
            "basegpu": "GPU instance",
        },
        "physical_cores": "physical cores = vCPU on highcore/hypercore, vCPU / 2 on highcpu, "
                          "standard, highmem, hypercpu. MPI-only solvers (OpenFOAM, DAFoam, "
                          "SU2, code_saturne, XBeach, TELEMAC, LIGGGHTS) run one process per "
                          "physical core.",
    }


@mcp.tool(annotations=READ)
async def solver_guide(solver: str) -> dict:
    """How to prepare a case for a solver on cloudHPC: required input files,
    what cloudHPC does automatically, parallelism, resources, log files.

    solver: script name (e.g. "EnergyPlus-9.6.0", "DAFoam-v5.0.0") or family
    ("fds", "openfoam", "dafoam", "calculix", "code_aster", "codesaturne", "su2",
    "openradioss", "contam", "energyplus", "liggghts", "openlb", "telemac", "swan",
    "xbeach", "custom"). Makes no API calls. Includes restart/relaunch behaviour
    (e.g. FDS restart and .stop files) and optional cloudHPC settings.
    Always call it when the user asks how to prepare, structure or run a case for a
    solver: it is the authoritative description of how cloudHPC runs that solver.
    """
    fam = solver if solver in guides.GUIDES else advisor.family_of(solver)
    g = guides.guide_for(fam)
    if not g:
        return {"error": f"No guide for '{solver}' yet.",
                "available": sorted(guides.GUIDES)}
    return {"family": fam, **g}


@mcp.tool(annotations=READ)
async def suggest_resources(
    solver: str,
    cells: int | None = None,
    nodes: int | None = None,
    elements: int | None = None,
    fds_meshes: int | None = None,
    fds_mpi_groups: int | None = None,
    fds_total_cells: int | None = None,
    case_folder: str | None = None,
    prefer_speed: bool = False,
    ctx: Context = None,
) -> dict:
    """Suggest vCPU and RAM type for a run, following cloudHPC scalability rules.

    solver: script name (e.g. "fds6.9.1", "openFoam-v2406") or family
            ("fds", "openfoam", "calculix", "code_aster", "openradioss", "su2").
    cells: CFD cells (OpenFOAM/SU2). nodes: FEA nodes (CalculiX/code_aster).
    elements: OpenRadioss elements.
    fds_*: FDS mesh info if the .fds file cannot be read directly.
    case_folder: (local mode) folder to inspect automatically instead.
    prefer_speed: favour the faster hypercore/hypercpu instances over cost.
    """
    family = solver if solver in advisor.FAMILIES else advisor.family_of(solver)
    fds_info = None
    if case_folder and LOCAL:
        try:
            info = advisor.inspect_case(case_folder)
        except FileNotFoundError as e:
            return _err(e)
        family = info["family"] if info["family"] != "other" else family
        fds_info = info.get("fds")
        cells = cells or info.get("cells")
        nodes = nodes or info.get("nodes")
    elif family == "fds" and (fds_meshes or fds_mpi_groups):
        groups = fds_mpi_groups or fds_meshes
        fds_info = {"meshes": fds_meshes or groups, "mpi_groups": groups,
                    "total_cells": fds_total_cells or 0,
                    "cells_per_group": [], "uses_mpi_process": bool(fds_mpi_groups),
                    "uses_mult_id": False}
    try:
        cpus, rams = await asyncio.gather(_catalog(ctx, "cpu"), _catalog(ctx, "ram"))
    except CloudHPCError as e:
        return _catalog_err(e)
    return advisor.suggest(family, cpus, rams, cells=cells, nodes=nodes,
                           elements=elements, fds=fds_info, prefer_speed=prefer_speed)


# -------------------------------------------------------------- storage

@mcp.tool(annotations=READ)
async def list_storage(folder: str = "", ctx: Context = None) -> dict:
    """List files and folders in the user's cloudHPC storage.

    folder: storage folder path ("" = root, e.g. "myCase" or "myCase/sub").
    """
    try:
        items = await client_for(ctx).list_storage(folder)
    except CloudHPCError as e:
        return _err(e)
    return {"folder": folder or "/", "items": [_storage_item(i) for i in items],
            "note": "Files are deleted automatically 60 days after creation."}


@mcp.tool(annotations=READ)
async def list_results(folder: str, ctx: Context = None) -> dict:
    """List the result archives of a case folder (FDS.tar.gz, OPENFOAM-*.tar.gz, ...)."""
    try:
        items = await client_for(ctx).list_storage(folder)
    except CloudHPCError as e:
        return _err(e)
    results = [_storage_item(i) for i in items
               if i.get("type") == "file" and files.is_result_file(i.get("name", ""))]
    out: dict[str, Any] = {"folder": folder, "results": results}
    if not results:
        out["note"] = ("No result archives yet. They are written at the end of the run, "
                       "every 10 hours while running, or after sync_simulation.")
    return out


@mcp.tool(annotations=READ)
async def get_download_link(path: str, ctx: Context = None) -> dict:
    """Get a temporary download link for a storage file (e.g. "myCase/FDS.tar.gz")."""
    try:
        url = await client_for(ctx).download_url(path)
    except CloudHPCError as e:
        return _err(e)
    name = os.path.basename(path)
    return {"path": path, "url": url,
            "curl": f"curl -L -o '{name}' '{url}'",
            "note": "The link is temporary and gives access to this file: do not share it."}


@mcp.tool(annotations=WRITE)
async def get_upload_link(storage_folder: str, filename: str, ctx: Context = None) -> dict:
    """Get a signed URL to upload one file into a storage folder (remote clients).

    For a case folder: compress the CONTENT of the folder (files at the archive
    root, no wrapping folder) into e.g. upload.tar.gz or case.zip, then upload it
    into a storage folder with the case name. Accepted: zip, tar.gz, 7z, rar, xz.
    """
    try:
        c = client_for(ctx)
        url = await c.upload_url(storage_folder, filename)
    except CloudHPCError as e:
        return _err(e)
    return {
        "storage_path": f"{storage_folder.strip('/')}/{filename}",
        "url": url,
        "curl": f"curl -X PUT -H 'Content-Type: application/octet-stream' "
                f"--upload-file '{filename}' '{url}'",
        "compress_hint": {
            "linux_mac": "cd CASE_FOLDER && tar -czf ../upload.tar.gz *",
            "windows_powershell": "Compress-Archive -Path CASE_FOLDER\\* -DestinationPath case.zip",
        },
        "note": "Upload the raw file (not multipart form). The link expires.",
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_storage(path: str, confirm: bool = False, ctx: Context = None) -> dict:
    """Delete a file or folder from storage. Irreversible.

    If the client supports it, the user confirms in a dialog shown by the server.
    Otherwise: call first with confirm=false, show the summary to the user and
    call again with confirm=true only after the user agrees in a new message,
    even if the original request was explicit.
    """
    c = client_for(ctx)
    try:
        info = await c.view_by_path(path)
    except CloudHPCError as e:
        return _err(e)
    summary = (f"Delete {info.get('type', 'item')} '{path}' from cloudHPC storage. "
               "This cannot be undone.")
    stop = await _confirmed(ctx, summary, confirm)
    if stop:
        return stop
    try:
        await c.delete_path(path)
    except CloudHPCError as e:
        return _err(e)
    return {"deleted": path}


# ----------------------------------------------------------- simulations

@mcp.tool(annotations=WRITE)
async def launch_simulation(
    solver: str,
    cpu: int,
    ram: str,
    folder: str,
    mesh_folder: str | None = None,
    regular_instance: bool = False,
    confirm: bool = False,
    ctx: Context = None,
) -> dict:
    """Launch a simulation on a case folder already in storage. Costs money:
    billed per vCPU-hour; the price is not available through the API, so do not
    promise a cost estimate (the actual cost appears in get_simulation after the run).

    solver: exact script name from list_solvers (e.g. "fds6.9.1").
    cpu / ram: from suggest_resources or list_machine_options.
    folder: storage folder with the case.
    mesh_folder: optional storage folder with a mesh to reuse (OpenFOAM).
    regular_instance: non-preemptible machine (more expensive, no interruptions).
    Call first with confirm=false, show the returned summary to the user, and
    call again with confirm=true only after explicit approval.
    """
    c = client_for(ctx)
    try:
        cpus, rams, scripts = await asyncio.gather(c.cpu_options(), c.ram_options(), c.scripts())
    except CloudHPCError as e:
        return _err(e)
    problems = []
    if solver not in scripts:
        close = [s for s in scripts if solver.lower().split("-")[0] in s.lower()][:10]
        problems.append(f"Unknown solver '{solver}'. Similar: {close}")
    if cpu not in cpus:
        problems.append(f"vCPU {cpu} not available. Options: {cpus}")
    if ram not in rams:
        problems.append(f"RAM type '{ram}' not available. Options: {rams}")
    fam = advisor.family_of(solver)
    chars = errors.bad_name(folder)
    if chars:
        problems.append(f"Folder name '{folder}' contains characters cloudHPC does not accept: "
                        f"{' '.join(chars)}")
    if fam == "openfoam" and cpu < (2 if ram in advisor.PHYSICAL_RAM else 4):
        problems.append("OpenFOAM always runs in parallel on cloudHPC: at least 2 vCPU on "
                        "highcore/hypercore.")
    if cpu == 1 and ram == "highcpu" and fam != "other":
        problems.append("1 vCPU highcpu has too little RAM for engineering solvers and fails "
                        "at start-up: use standard (or more vCPU).")
    if fam in advisor.NO_HYPERTHREAD and ram not in advisor.PHYSICAL_RAM:
        problems.append(f"{solver} does not use hyperthreading: use highcore or hypercore.")
    warnings: list[str] = []
    case = UPLOADED_CASES.get(folder.strip("/")) or {}
    if fam == "fds":
        p, w = _fds_core_problem(case, cpu, ram)
        if p:
            problems.append(p)
        if w:
            warnings.append(w)
    if fam == "fds" and not regular_instance and case.get("fds") \
            and case["fds"].get("dt_restart") is None:
        warnings.append("The .fds has no DT_RESTART: on a preemptible instance a restart "
                        "begins again from time 0. Add DT_RESTART on &DUMP, or use "
                        "regular_instance.")
    if fam == "code_aster" and case.get("comm_parallel") and case.get("mpi_nbcpu"):
        if cpu < 2 * case["mpi_nbcpu"]:
            problems.append(f"code_aster: mpi_nbcpu is {case['mpi_nbcpu']} in the .export, so at "
                            f"least {2 * case['mpi_nbcpu']} vCPU are needed (cloudHPC gives each "
                            "MPI process vCPU / mpi_nbcpu / 2 OpenMP threads).")
    if fam == "energyplus":
        idf_v = (UPLOADED_CASES.get(folder.strip("/")) or {}).get("idf_version")
        solver_v = solver.split("-", 1)[1] if "-" in solver else ""
        if idf_v and solver_v and idf_v.split(".")[:2] != solver_v.split(".")[:2]:
            problems.append(f"The IDF is version {idf_v} but {solver} was selected: the Version "
                            "must match. Choose the matching EnergyPlus version or convert the "
                            "model with IDFVersionUpdater.")
    if fam == "dafoam":
        nsub = (UPLOADED_CASES.get(folder.strip("/")) or {}).get("number_of_subdomains")
        cores = cpu if ram in advisor.PHYSICAL_RAM else max(cpu // 2, 1)
        if nsub and nsub != cores:
            problems.append(f"DAFoam: numberOfSubdomains is {nsub} in system/decomposeParDict but "
                            f"{cpu} vCPU on {ram} give {cores} MPI processes, and cloudHPC does "
                            f"not adjust it for DAFoam. Use {nsub} vCPU on highcore/hypercore or "
                            "change numberOfSubdomains.")
    if problems:
        return {"error": "Launch blocked by the MCP server's own checks (nothing was sent "
                         "to cloudHPC, nothing started or billed).", "problems": problems}

    summary = (f"Run {solver} on {cpu} vCPU ({ram}"
               f"{', regular instance' if regular_instance else ', preemptible'}) "
               f"using storage folder '{folder}'"
               f"{f' with mesh from {mesh_folder}' if mesh_folder else ''}. "
               "Billed per vCPU-hour until the run ends or is stopped; the actual cost is "
               "shown when the run ends.")
    if warnings:
        summary += " Note: " + " ".join(warnings)
    stop = await _confirmed(ctx, summary, confirm)
    if stop:
        return stop
    try:
        sim_id = await c.add_simulation(cpu, ram, solver, folder, mesh_folder, regular_instance)
    except CloudHPCError as e:
        return _err(e)
    return {"launched": True, "simulation_id": sim_id, "summary": summary,
            "next": "Use get_simulation or wait_for_simulation to follow it."}


@mcp.tool(annotations=READ)
async def list_simulations(
    status: Literal["active", "all", "completed", "error", "stopped"] = "active",
    limit: int = 20,
    ctx: Context = None,
) -> dict:
    """List the user's simulations, most recent first.

    status: "active" (pending/running/stopping), "all", "completed", "error", "stopped".
    """
    try:
        sims = await client_for(ctx).list_simulations()
    except CloudHPCError as e:
        return _err(e)
    wanted = {"active": ACTIVE_STATUSES, "completed": {10}, "error": {60}, "stopped": {50}}
    if status != "all":
        sims = [s for s in sims if s.get("status") in wanted[status]]
    sims = sorted(sims, key=lambda s: s.get("id", 0), reverse=True)[: max(1, min(limit, 100))]
    return {"simulations": [_sim_summary(s) for s in sims]}


@mcp.tool(annotations=READ)
async def get_simulation(simulation_id: int, include_log: bool = False, ctx: Context = None) -> dict:
    """Get status and details of a simulation.

    include_log: add the last output/log lines and a diagnosis of known cloudHPC
    errors and warnings with their fix. For FDS it also adds fds_progress
    (simulated time vs T_END, percent, automatic restarts). Use it whenever a run ends, since a run
    can be COMPLETED even if the solver failed.
    """
    c = client_for(ctx)
    try:
        s = await (c.get_simulation_full(simulation_id) if include_log else c.get_simulation(simulation_id))
    except CloudHPCError as e:
        return _err(e)
    out = _sim_summary(s)
    if include_log:
        for key in ("logs", "output"):
            text = s.get(key) or ""
            if text:
                out[key] = text[-4000:]
        out.update(_diagnosis(s))
    return out


@mcp.tool(annotations=READ)
async def wait_for_simulation(
    simulation_id: int,
    max_minutes: int = 10,
    poll_seconds: int = 120,
    ctx: Context = None,
) -> dict:
    """Wait until a simulation is no longer pending/running, or until max_minutes.

    Polls every poll_seconds (min 60, to respect API rate limits). If it
    returns still running, call it again later rather than looping quickly.
    """
    c = client_for(ctx)
    max_minutes = max(1, min(max_minutes, 30))
    poll_seconds = max(60, poll_seconds)
    deadline = time.monotonic() + max_minutes * 60
    while True:
        try:
            s = await c.get_simulation(simulation_id)
        except CloudHPCError as e:
            return _err(e)
        finished = s.get("status") not in ACTIVE_STATUSES
        if finished or time.monotonic() + poll_seconds > deadline:
            if finished:
                try:  # one extra call: COMPLETED runs can still contain errors
                    s = await c.get_simulation_full(simulation_id)
                except CloudHPCError:
                    pass
            out = _sim_summary(s)
            out["finished"] = finished
            if finished:
                out.update(_diagnosis(s))
            return out
        if ctx is not None:
            try:
                await ctx.report_progress(0, None, f"{STATUS.get(s.get('status'))}")
            except Exception:
                pass
        await asyncio.sleep(poll_seconds)


@mcp.tool(annotations=WRITE)
async def sync_simulation(simulation_id: int, ctx: Context = None) -> dict:
    """Ask a running simulation to upload partial results to storage now."""
    try:
        await client_for(ctx).sync_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    return {"requested": True,
            "note": "Partial results appear in the case folder after a few minutes."}


@mcp.tool(annotations=DESTRUCTIVE)
async def stop_simulation(
    simulation_id: int,
    mode: Literal["soft", "hard"] = "soft",
    confirm: bool = False,
    ctx: Context = None,
) -> dict:
    """Stop a running simulation.

    soft: the solver stops cleanly and results are saved (recommended).
    hard: immediate termination.
    Call first with confirm=false and show the summary; confirm=true only after
    the user agrees.
    """
    c = client_for(ctx)
    try:
        s = await c.get_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    summary = (f"{mode.upper()} stop of simulation {simulation_id} "
               f"({s.get('script')} on {s.get('cpu')} vCPU, folder '{s.get('folder')}', "
               f"status {STATUS.get(s.get('status'))}).")
    if s.get("status") not in ACTIVE_STATUSES:
        return {"error": f"Simulation is not active: {STATUS.get(s.get('status'))}"}
    if mode == "hard":   # immediate kill: always ask the user
        stop = await _confirmed(ctx, summary, confirm)
        if stop:
            return stop
    elif not confirm:    # soft stop: two-step confirmation is enough
        return {"confirmation_required": True, "summary": summary}
    try:
        await c.stop_simulation(simulation_id, hard=(mode == "hard"))
    except CloudHPCError as e:
        return _err(e)
    return {"stopping": True, "summary": summary}


@mcp.tool(annotations=WRITE)
async def start_desktop(
    folder: str,
    os_type: Literal["linux", "windows"] = "linux",
    cpu: int = 4,
    ram: str = "standard",
    confirm: bool = False,
    ctx: Context = None,
) -> dict:
    """Start a remote desktop machine (static instance) for post-processing,
    e.g. ParaView or Smokeview on the results of a storage folder.

    folder: storage folder to work on. os_type: "linux" (default) or "windows"
    (Windows adds a Windows licence cost). Only on activated accounts.
    The machine keeps running, and is billed per vCPU-hour, until it is stopped
    with stop_simulation: it never stops by itself. Call first with
    confirm=false, show the summary to the user, then call again with
    confirm=true only after explicit approval. When it is RUNNING, get the link
    with open_remote_desktop.
    """
    c = client_for(ctx)
    try:
        cpus, rams, scripts = await asyncio.gather(c.cpu_options(), c.ram_options(), c.scripts())
    except CloudHPCError as e:
        return _err(e)
    wanted = "ubuntu-static" if os_type == "linux" else "windows-static"
    script = next((x for x in scripts if wanted in x.lower()), None)
    problems = []
    if not script:
        problems.append(f"No {os_type} desktop script available. Desktop scripts: "
                        f"{[x for x in scripts if 'static' in x.lower()]}")
    if cpu not in cpus:
        problems.append(f"vCPU {cpu} not available. Options: {cpus}")
    if ram not in rams:
        problems.append(f"RAM type '{ram}' not available. Options: {rams}")
    if cpu == 1 and ram == "highcpu":
        problems.append("1 vCPU highcpu has too little RAM for a desktop: use standard.")
    chars = errors.bad_name(folder)
    if chars:
        problems.append(f"Folder name '{folder}' contains characters cloudHPC does not "
                        f"accept: {' '.join(chars)}")
    if problems:
        return {"error": "Desktop not started (nothing was sent to cloudHPC).",
                "problems": problems}
    summary = (f"Start a {'Windows' if os_type == 'windows' else 'Linux'} remote desktop "
               f"({script}) on {cpu} vCPU ({ram}) with storage folder '{folder}'. It keeps "
               "running and is billed per vCPU-hour until you stop it: it does not stop by "
               "itself.")
    if os_type == "windows":
        summary += " Windows desktops also include a Windows licence cost."
    stop = await _confirmed(ctx, summary, confirm)
    if stop:
        return stop
    try:
        sim_id = await c.add_simulation(cpu, ram, script, folder)
    except CloudHPCError as e:
        return _err(e)
    return {"started": True, "simulation_id": sim_id, "summary": summary,
            "next": "When get_simulation shows RUNNING (a few minutes), get the link with "
                    "open_remote_desktop. Stop it with stop_simulation when finished: it "
                    "is billed until then."}


RESUMABLE = {"fds", "openfoam"}   # resume tested on cloudHPC for these solvers


@mcp.tool(annotations=WRITE)
async def resume_simulation(simulation_id: int, confirm: bool = False,
                            ctx: Context = None) -> dict:
    """Resume a stopped or interrupted simulation: launch it again on the same
    storage folder with the SAME solver, vCPU, RAM type and instance type, so
    the solver continues from its restart data (FDS .restart files, OpenFOAM
    latest time). Tested for FDS and OpenFOAM only. vCPU and RAM are never
    changed: changing them on a restart is not recommended.
    See https://docs.cloudhpc.cloud/simulation/#restart . Costs money: call
    first with confirm=false, show the summary, then confirm=true after approval.
    """
    c = client_for(ctx)
    try:
        s = await c.get_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    if s.get("status") in ACTIVE_STATUSES:
        return {"error": f"Simulation {simulation_id} is still "
                         f"{STATUS.get(s.get('status'))}: it can be resumed only after it stops."}
    script, folder = str(s.get("script", "")), str(s.get("folder", "")).strip("/")
    fam = advisor.family_of(script)
    if fam not in RESUMABLE:
        return {"error": f"Resume is tested on cloudHPC only for FDS and OpenFOAM, not for "
                         f"{script}. Each solver resumes differently: check its restart "
                         "settings, then launch it again with launch_simulation on the same "
                         "folder, solver, vCPU and RAM type.",
                "docs": "https://docs.cloudhpc.cloud/simulation/#restart"}
    notes = []
    if fam == "fds":
        try:
            items = await c.list_storage(folder)
        except CloudHPCError as e:
            return _err(e)
        if not any(str(i.get("basename", "")).endswith(".restart") for i in items):
            return {"error": "No .restart files in the storage folder: FDS would start again "
                             "from time 0, so this is not a resume. FDS writes restart files "
                             "only if DT_RESTART is set on &DUMP.",
                    "folder": folder, "hint": MISSING_FILES_HINT if not items else None}
        notes.append("FDS finds the .restart files and adds RESTART=.TRUE. automatically.")
    else:
        notes.append("OpenFOAM continues from the latest time saved in the folder "
                     "(startFrom latestTime is enforced).")
    cpu, ram, regular = int(s.get("cpu")), str(s.get("ram")), bool(s.get("nopre"))
    summary = (f"Resume simulation {simulation_id}: run {script} again on {cpu} vCPU ({ram}"
               f"{', regular instance' if regular else ', preemptible'}) using storage folder "
               f"'{folder}'. Billed per vCPU-hour until it ends or is stopped. "
               + " ".join(notes))
    stop = await _confirmed(ctx, summary, confirm)
    if stop:
        return stop
    try:
        sim_id = await c.add_simulation(cpu, ram, script, folder, s.get("mesh") or None, regular)
    except CloudHPCError as e:
        return _err(e)
    return {"resumed": True, "simulation_id": sim_id, "previous_simulation_id": simulation_id,
            "summary": summary,
            "next": "Use get_simulation or wait_for_simulation to follow it."}


# ------------------------------------------------- live updates (_push)

async def _read_storage_text(c: CloudHPCClient, path: str) -> str | None:
    """Current content of a small storage file, None if missing or unreadable."""
    try:
        return await c.get_text(await c.download_url(path, attempts=3), live.MAX_BYTES)
    except CloudHPCError:
        return None


def _latest(items: list[dict], n: int = 5) -> list[str]:
    return sorted((str(i.get("basename")) for i in items), reverse=True)[:n]


@mcp.tool(annotations=READ)
async def live_settings(simulation_id: int, path: str | None = None,
                        ctx: Context = None) -> dict:
    """What can be changed in a RUNNING simulation without stopping it, whether
    an update is still waiting to be applied and, with path, the current
    content of one of those files (as of the last sync).

    Supported: OpenFOAM (system/controlDict, fvSolution, fvSchemes), FDS
    (<CHID>.stop), code_saturne (RESU/<run>/control_file), OpenRadioss
    (<root>_0001.ctl). Read the CURRENT file here before editing it with
    update_running_simulation: cloudHPC adjusts some inputs at start (e.g. the
    OpenFOAM controlDict), so the user's local copy can differ.
    """
    c = client_for(ctx)
    try:
        s = await c.get_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    script, folder = str(s.get("script", "")), str(s.get("folder", "")).strip("/")
    fam = advisor.family_of(script)
    info = live.describe(fam)
    if info is None:
        return {"supported": False, "solver": script,
                "note": f"{script} reads its input only at start: it cannot be changed while "
                        "running. Stop it, change the case and launch it again (or resume it). "
                        f"Live updates are available for: {', '.join(live.SUPPORTED)}."}
    out: dict[str, Any] = {"supported": True, "simulation_id": simulation_id, "solver": script,
                           "status": STATUS.get(s.get("status")), "folder": folder, **info}
    if s.get("status") != 30:
        out["note"] = "Live updates are possible only while the simulation is RUNNING."
    try:
        items = await c.list_storage(folder)
    except CloudHPCError as e:
        return _err(e)
    if any(i.get("type") == "dir" and i.get("basename") == live.PUSH_DIR for i in items):
        out["pending"] = True
        out["pending_note"] = ("An update is waiting in _push for the sync of the machine "
                               "(a few minutes). When it is applied, _push disappears and "
                               "the simulation output (get_simulation with include_log=true) shows a 'push "
                               "applied' line. If it stays there, "
                               "run sync_simulation.")
    if path:
        p = live.clean_path(path)
        if not p or not live.is_allowed(fam, p):
            out["error"] = f"'{path}' cannot be changed live for {script}: see files."
            return out
        text = await _read_storage_text(c, f"{folder}/{p}")
        if text is None:
            out["content_note"] = (f"{p} is not in the storage yet: run sync_simulation, wait "
                                   "a few minutes and try again.")
        else:
            out["path"], out["content"] = p, text
    return out


@mcp.tool(annotations=WRITE)
async def update_running_simulation(simulation_id: int, files: dict[str, str],
                                    confirm: bool = False, ctx: Context = None) -> dict:
    """Change settings of a RUNNING simulation without stopping it.

    files: {relative path in the case folder: full new file content}, e.g.
    {"system/controlDict": "..."}; only the files listed by live_settings for
    the solver are accepted (max 5 files, 256 KB each). Send the WHOLE file,
    based on the current one from live_settings(path=...). The files go to
    <folder>/_push/ in the storage, then a sync of the simulation is requested
    automatically: the machine copies them into the case and the solver picks
    them up. Do not call sync_simulation as well.
    Changes such as a later endTime make the run longer and cost more. Call
    first with confirm=false, show the summary and the diff to the user, then
    confirm=true only after explicit approval.
    """
    c = client_for(ctx)
    try:
        s = await c.get_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    script, folder = str(s.get("script", "")), str(s.get("folder", "")).strip("/")
    fam = advisor.family_of(script)
    if live.rule_for(fam) is None:
        return {"error": f"{script} cannot be changed while running. Live updates are "
                         f"available for: {', '.join(live.SUPPORTED)}."}
    if s.get("status") != 30:
        return {"error": f"Simulation {simulation_id} is {STATUS.get(s.get('status'))}: live "
                         "updates are possible only while it is RUNNING."}
    if not files:
        return {"error": "No files given."}
    if len(files) > live.MAX_FILES:
        return {"error": f"At most {live.MAX_FILES} files per update."}
    clean: dict[str, str] = {}
    problems: list[str] = []
    for raw, text in files.items():
        p = live.clean_path(raw)
        if not p or not live.is_allowed(fam, p):
            problems.append(f"'{raw}' cannot be changed live for {script}. Allowed: "
                            f"{', '.join(live.describe(fam)['files'])}.")
        else:
            clean[p] = text
    if problems:
        return {"error": "Nothing was sent.", "problems": problems}
    try:
        items = await c.list_storage(folder)
    except CloudHPCError as e:
        return _err(e)
    folder_files = {str(i.get("basename")) for i in items if i.get("type") == "file"}
    pending = any(i.get("type") == "dir" and i.get("basename") == live.PUSH_DIR for i in items)
    notes: list[str] = []
    diffs: dict[str, str] = {}
    for p, text in clean.items():
        current = None
        if fam in ("openfoam", "codesaturne"):
            current = await _read_storage_text(c, f"{folder}/{p}")
        if fam == "codesaturne":
            try:
                await c.view_by_path(f"{folder}/{p.rsplit('/', 1)[0]}")
            except CloudHPCError:
                problems.append(f"Run folder {p.rsplit('/', 1)[0]} not found in the storage "
                                "(list_storage shows the RESU/<run> folders after a sync).")
        errs, extra = live.check(fam, p, text, current, folder_files)
        problems += errs
        notes += extra
        d = live.diff(p, current, text)
        if d:
            diffs[p] = d
        elif fam in ("openfoam", "codesaturne"):
            notes.append(f"{p}: the current file is not in the storage, so no diff is shown "
                         "(sync_simulation uploads it).")
    if problems:
        return {"error": "Nothing was sent.", "problems": problems}
    if all(d == "(no change)" for d in diffs.values()) and len(diffs) == len(clean):
        return {"error": "The new files are identical to the current ones: nothing to send."}
    if pending:
        notes.append("A previous update is still waiting in _push: both are applied "
                     "together (same file: the newer one wins).")
    summary = (f"Update running simulation {simulation_id} ({script}, folder '{folder}'): "
               f"upload {', '.join(clean)} and sync the simulation so the machine copies "
               "them into the case now. " + " ".join(notes))
    shown = summary + ("\n\n" + "\n\n".join(diffs.values()) if diffs else "")
    stop = await _confirmed(ctx, shown, confirm)
    if stop:
        if diffs:
            stop["diff"] = diffs
        return stop
    sent = []
    try:
        for p, text in clean.items():
            # the API takes the case folder as dirname; sub-folders go in the file name
            await c.put_bytes(await c.upload_url(folder, f"{live.PUSH_DIR}/{p}"),
                              text.encode())
            sent.append(p)
        await c.refresh_cache()
    except CloudHPCError as e:
        out = _err(e)
        out["sent_before_error"] = sent
        return out
    out = {"sent": sent, "storage_folder": f"{folder}/{live.PUSH_DIR}", "summary": summary}
    try:
        await c.sync_simulation(simulation_id)
        out["sync_requested"] = True
        out["next"] = ("The sync copies the files into the case within a few minutes: then "
                       "_push disappears from the storage and the simulation output "
                       "(get_simulation with include_log=true) shows 'push applied'. Applied "
                       + live.rule_for(fam)["applied_when"] + ".")
    except CloudHPCError as e:
        out["sync_requested"] = False
        out["sync_error"] = str(e)
        out["next"] = ("The files are in _push but the sync request failed: they are applied "
                       "at the next sync (sync_simulation can be called again).")
    out["note"] = "The user's local copy of these files is now out of date."
    return out


@mcp.tool(annotations=READ)
async def open_remote_desktop(simulation_id: int, ctx: Context = None) -> dict:
    """Get the browser remote-desktop (VNC) link of a running simulation.

    The link is short-lived and personal: give it only to the user.
    """
    try:
        s = await client_for(ctx).get_simulation(simulation_id)
    except CloudHPCError as e:
        return _err(e)
    if s.get("status") != 30 or not s.get("vnc_url"):
        return {"error": "Remote desktop is available only while the simulation is RUNNING."}
    return {"url": s["vnc_url"],
            "note": "Short-lived link; ask again if it expires. The desktop needs a few "
                    "minutes after the run starts: if it does not open yet, retry shortly."}


@mcp.tool(annotations=READ)
async def api_usage(ctx: Context = None) -> dict:
    """Show the API rate limits and how many API requests have been made.

    rate_limits: as reported by cloudHPC (hourly/daily window, whole account).
    this_session (local mode): exact count of API requests made by this MCP
    server since it started, by endpoint. File transfers through temporary
    links are not API requests and are not counted.
    """
    c = client_for(ctx)
    try:
        await c.ram_options()  # cheap call to read the rate-limit headers
    except CloudHPCError as e:
        return _err(e)
    out: dict[str, Any] = {"rate_limits": c.rate.as_dict()}
    if LOCAL:
        out["this_session"] = {
            "since_minutes": round((time.time() - STARTED_AT) / 60, 1),
            "api_requests": sum(REQUEST_LOG.values()),
            "by_endpoint": dict(REQUEST_LOG.most_common()),
        }
    return out


# ---------------------------------------------------- local-only tools

if LOCAL:

    @mcp.tool(annotations=READ)
    async def inspect_case(folder: str) -> dict:
        """Inspect a local case folder: detect the solver and the model size.

        Reads .fds meshes (cells, MPI_PROCESS groups), OpenFOAM polyMesh cells,
        CalculiX nodes, code_aster mpi_nbcpu, and runs pre-flight checks for the
        most common cloudHPC errors (folder name, MPI_PROCESS order,
        decomposeParDict, missing files). Makes no API calls. Fix 'error'
        items before uploading.
        """
        try:
            info = advisor.inspect_case(folder)
        except (FileNotFoundError, OSError) as e:
            return _err(e)
        info["preflight"] = errors.preflight(info)
        return info

    @mcp.tool(annotations=WRITE)
    async def upload_folder(folder: str, storage_folder: str | None = None,
                            ignore_preflight: bool = False,
                            ctx: Context = None) -> dict:
        """Compress a local case folder and upload it to cloudHPC storage.

        The CONTENT of the folder goes into upload.tar.gz, uploaded into the
        storage folder storage_folder (default: the local folder name).
        Hidden files are skipped. Returns the storage folder to use in
        launch_simulation.

        The pre-flight checks of inspect_case always run first: if they find
        errors (e.g. MPI_PROCESS order, decomposeParDict method) nothing is
        uploaded. Explain the problems to the user and fix them; use
        ignore_preflight=true only if the user explicitly wants to upload anyway.
        """
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            return _err(FileNotFoundError(f"Folder not found: {folder}"))
        if not os.listdir(folder):
            return _err(ValueError(f"Folder is empty: {folder}"))
        target = (storage_folder or os.path.basename(folder.rstrip(os.sep))).strip("/")
        chars = errors.bad_name(target)
        if chars:
            return {"error": f"Storage folder name '{target}' contains characters cloudHPC does "
                             f"not accept: {' '.join(chars)}. Pass storage_folder with a clean name."}
        try:
            info = advisor.inspect_case(folder)
            issues = errors.preflight(info)
        except OSError:
            info, issues = {}, []
        if storage_folder:  # a clean storage name overrides the local folder name
            issues = [i for i in issues if "Folder name" not in i["issue"]]
        blocking = [i for i in issues if i["severity"] == "error"]
        if blocking and not ignore_preflight:
            return {"error": "Pre-flight checks failed: nothing was uploaded. Fix these "
                             "problems first (or pass ignore_preflight=true if the user "
                             "explicitly wants to upload anyway).",
                    "problems": blocking,
                    "other_notes": [i for i in issues if i["severity"] != "error"]}
        c = client_for(ctx)
        try:
            with tempfile.TemporaryDirectory(prefix="cloudhpc-") as tmp:
                archive = await asyncio.to_thread(files.make_case_archive, folder, tmp)
                size = os.path.getsize(archive)
                url = await c.upload_url(target, files.UPLOAD_ARCHIVE)
                await c.put_file(url, archive)
            await c.refresh_cache()
        except CloudHPCError as e:
            return _err(e)
        UPLOADED_CASES[target] = info
        out = {"uploaded": True, "storage_folder": target,
               "archive": f"{target}/{files.UPLOAD_ARCHIVE}",
               "size_mb": round(size / 1e6, 2)}
        if issues:
            out["preflight_notes"] = issues
        return out

    @mcp.tool(annotations=WRITE)
    async def download_results(folder: str, local_dir: str, extract: bool = True,
                               files_to_get: list[str] | None = None,
                               overwrite: bool = False,
                               ctx: Context = None) -> dict:
        """Download result archives of a storage folder to a local directory.

        folder: storage folder of the case. local_dir: where to save (created
        if missing). extract: unpack .tar.gz archives and delete them after.
        files_to_get: optional list of file names; default = all result archives.
        overwrite: replace files that already exist in local_dir. Default false:
        existing files (e.g. the user's input dictionaries) are kept and listed in
        kept_existing, because cloudHPC returns some inputs modified (OpenFOAM
        decomposeParDict, controlDict). Usually extract into the case folder.
        After extracting, the solver's own logs are checked (OpenFOAM log.* must
        end with 'End' and have no FOAM FATAL error; FDS .out must report
        'FDS completed successfully'): see solver_ok / solver_logs.
        """
        c = client_for(ctx)
        local_dir = os.path.abspath(os.path.expanduser(local_dir))
        os.makedirs(local_dir, exist_ok=True)
        try:
            items = await c.list_storage(folder)
        except CloudHPCError as e:
            return _err(e)
        names = [i["basename"] for i in items if i.get("type") == "file"]
        wanted = files_to_get or [n for n in names if files.is_result_file(n)]
        if not wanted:
            return {"error": f"No result archives in '{folder}' yet.", "files_in_folder": names}
        done = []
        for name in wanted:
            if name not in names:
                done.append({"file": name, "error": "not found in storage folder",
                             "hint": MISSING_FILES_HINT})
                continue
            dest = os.path.join(local_dir, name)
            try:
                url = await c.download_url(f"{folder.strip('/')}/{name}")
                size = await c.get_file(url, dest)
                entry: dict[str, Any] = {"file": name, "size_mb": round(size / 1e6, 2)}
                if extract and name.endswith((".tar.gz", ".tgz", ".tar")):
                    kept: list[str] = []
                    entry["extracted_entries"] = await asyncio.to_thread(
                        files.safe_extract, dest, local_dir, overwrite, kept)
                    if kept:
                        entry["kept_existing"] = kept[:50]
                        entry["kept_existing_count"] = len(kept)
                    os.remove(dest)
                else:
                    entry["saved_as"] = dest
                done.append(entry)
            except (CloudHPCError, ValueError, OSError) as e:
                done.append({"file": name, "error": str(e)})
        out = {"local_dir": local_dir, "downloads": done}
        if extract:
            logs = errors.check_solver_logs(local_dir)
            if logs:
                out["solver_logs"] = logs
                bad = [l for l in logs if l["status"] != "ok"]
                out["solver_ok"] = not bad
                if bad:
                    out["warning"] = ("Some solver logs show errors or did not finish: "
                                      + ", ".join(l["file"] for l in bad))
        return out


# ------------------------------------------------ titles and annotations
# Every tool has a human-readable title and is marked either read-only or
# destructive (directories such as Claude's require it; clients use it to
# decide when to ask the user for confirmation).

TOOL_TITLES = {
    "list_solvers": "List solvers",
    "list_machine_options": "List machine options",
    "solver_guide": "Solver guide",
    "suggest_resources": "Suggest vCPU and RAM",
    "list_storage": "List storage",
    "list_results": "List result files",
    "get_download_link": "Get download link",
    "get_upload_link": "Get upload link",
    "delete_storage": "Delete from storage",
    "launch_simulation": "Launch simulation",
    "list_simulations": "List simulations",
    "get_simulation": "Get simulation",
    "wait_for_simulation": "Wait for simulation",
    "sync_simulation": "Sync partial results",
    "stop_simulation": "Stop simulation",
    "open_remote_desktop": "Open remote desktop",
    "api_usage": "API usage",
    "inspect_case": "Inspect local case",
    "upload_folder": "Upload case folder",
    "download_results": "Download results",
    "start_desktop": "Start remote desktop",
    "resume_simulation": "Resume simulation",
    "live_settings": "Read live settings",
    "update_running_simulation": "Update running simulation",
}
# change data or cost money: always confirmed by the client
DESTRUCTIVE_TOOLS = {"delete_storage", "launch_simulation", "stop_simulation",
                     "sync_simulation", "upload_folder", "download_results",
                     "start_desktop", "resume_simulation", "update_running_simulation"}
# not offered to read-only OAuth sign-ins (also tools that give write access)
READONLY_BLOCKED = DESTRUCTIVE_TOOLS | {"get_upload_link", "open_remote_desktop"}

for _t in mcp._tool_manager.list_tools():
    _t.title = TOOL_TITLES.get(_t.name, _t.name.replace("_", " ").capitalize())
    _destructive = _t.name in DESTRUCTIVE_TOOLS
    _t.annotations = ToolAnnotations(title=_t.title, readOnlyHint=not _destructive,
                                     destructiveHint=_destructive, openWorldHint=True)


# ------------------------------------------------------- public mode

if PUBLIC:
    for _t in list(mcp._tool_manager.list_tools()):
        if _t.name not in PUBLIC_TOOLS:
            mcp.remove_tool(_t.name)


def client_ip(scope: dict, trusted_proxies: int = 0) -> str:
    """Client address. Behind a proxy (Cloud Run) the real client is the
    right-most X-Forwarded-For entry added by the proxy: entries further left
    are sent by the client and can be forged."""
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            hops = [h.strip() for h in value.decode("latin-1").split(",") if h.strip()]
            if hops:
                return hops[max(len(hops) - 1 - trusted_proxies, 0)]
    client = scope.get("client")
    return client[0] if client else "unknown"


class PerIPRateLimit:
    """ASGI middleware: at most `per_minute` HTTP requests per client IP in any
    60-second window (in memory, per server instance). Answers 429 above it."""

    def __init__(self, app, per_minute: int, trusted_proxies: int = 0,
                 clock=time.monotonic):
        self.app, self.limit, self.trusted = app, per_minute, trusted_proxies
        self.clock = clock
        self.hits: dict[str, deque] = {}

    def allow(self, ip: str) -> tuple[bool, int]:
        now = self.clock()
        q = self.hits.setdefault(ip, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(int(60 - (now - q[0])) + 1, 1)
        q.append(now)
        if len(self.hits) > 10_000:  # forget idle addresses
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= 60]:
                del self.hits[k]
        return True, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.limit <= 0:
            return await self.app(scope, receive, send)
        ok, retry = self.allow(client_ip(scope, self.trusted))
        if ok:
            return await self.app(scope, receive, send)
        body = b'{"error": "Too many requests: slow down and retry later."}'
        await send({"type": "http.response.start", "status": 429,
                    "headers": [(b"content-type", b"application/json"),
                                (b"retry-after", str(retry).encode()),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------- entry

def main() -> None:
    if LOCAL:
        mcp.run(transport="stdio")
    elif PUBLIC:
        import uvicorn

        if not os.environ.get("CLOUDHPC_CATALOG_APIKEY", "").strip():
            logging.warning("CLOUDHPC_CATALOG_APIKEY is not set: list_solvers, "
                            "list_machine_options and suggest_resources will fail.")
        app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=HOST)
        uvicorn.run(PerIPRateLimit(app, PUBLIC_RATE_PER_MIN), host=HOST, port=PORT,
                    log_level="warning")
    else:
        import uvicorn

        uvicorn.run(remote_app(), host=HOST, port=PORT, log_level="warning")


def remote_app():
    """ASGI app of the hosted endpoint: MCP on /mcp plus, when
    CLOUDHPC_OAUTH_SECRET is set, OAuth sign-in for assistants that need it."""
    from . import oauth

    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host=HOST)
    provider = oauth.from_env()
    if provider is None:
        return app
    app.router.routes[:0] = provider.routes()
    return oauth.AuthGate(app, provider, blocked_tools=READONLY_BLOCKED)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""End-to-end test of the MCP tools against the real cloudHPC API.

It uses YOUR account: it uploads a tiny FDS case and, unless --no-launch is
given, runs it on 1 vCPU for about a minute (small cost).

Steps:
  1. read-only: solvers, machine options, storage root, simulations, usage
  2. creates a tiny FDS case (1 mesh, 2 s) in a temp folder
  3. inspect_case + suggest_resources
  4. upload_folder -> storage folder mcp-e2e-<timestamp>
  5. launch_simulation (1 vCPU, standard) with the newest FDS version
  6. wait_for_simulation (up to --wait minutes)
  7. list_results + download_results into ./e2e_results/
  8. with --cleanup: delete the storage folder

Usage:
  pip install -e .
  export CLOUDHPC_APIKEY=...
  python3 scripts/e2e_test.py --confirm-costs [--wait 20] [--cleanup] [--solver fds6.9.1]
  python3 scripts/e2e_test.py --no-launch [--cleanup]      # upload only, no run
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time

os.environ["CLOUDHPC_MCP_MODE"] = "local"
# requires the package installed: pip install -e <path to cloudhpc-mcp>

from cloudhpc_mcp import server  # noqa: E402

FDS_CASE = """&HEAD CHID='mcp_e2e', TITLE='cloudHPC MCP end-to-end test' /
&TIME T_END=2.0 /
&MESH ID='m1', IJK=20,20,20, XB=0.0,2.0,0.0,2.0,0.0,2.0 /
&REAC FUEL='PROPANE', SOOT_YIELD=0.01 /
&SURF ID='FIRE', HRRPUA=500.0 /
&OBST XB=0.8,1.2,0.8,1.2,0.0,0.1, SURF_IDS='FIRE','INERT','INERT' /
&VENT MB='XMIN', SURF_ID='OPEN' /
&VENT MB='XMAX', SURF_ID='OPEN' /
&DEVC ID='T1', QUANTITY='TEMPERATURE', XYZ=1.0,1.0,1.8 /
&TAIL /
"""

results = []


def step(name, out):
    ok = isinstance(out, dict) and "error" not in out
    results.append((name, ok))
    print(f"\n[{'PASS' if ok else 'FAIL'}] {name}")
    print(json.dumps(out, indent=2, default=str)[:1500])
    return out


def pick_fds(solvers):
    fds = solvers.get("solvers", {}).get("fds", [])
    def ver(s):
        return tuple(int(x) for x in s.replace("fds", "").split(".") if x.isdigit())
    return sorted(fds, key=ver)[-1] if fds else None


async def main(args):
    from cloudhpc_mcp.client import DEFAULT_API_URL
    print(f"API: {os.environ.get('CLOUDHPC_API_URL', DEFAULT_API_URL)}")
    if not args.no_launch and not args.confirm_costs:
        sys.exit("This test launches a small simulation on your account. "
                 "Add --confirm-costs to proceed, or --no-launch to only upload.")
    solvers = step("list_solvers", await server.list_solvers())
    step("list_machine_options", await server.list_machine_options())
    step("list_storage root", await server.list_storage())
    step("list_simulations all", await server.list_simulations("all", limit=5))
    step("api_usage", await server.api_usage())

    folder_name = f"mcp-e2e-{time.strftime('%Y%m%d-%H%M%S')}"
    with tempfile.TemporaryDirectory() as tmp:
        case = os.path.join(tmp, folder_name)
        os.makedirs(case)
        with open(os.path.join(case, "mcp_e2e.fds"), "w") as f:
            f.write(FDS_CASE)

        step("inspect_case", await server.inspect_case(case))
        step("suggest_resources", await server.suggest_resources("fds", case_folder=case))
        up = step("upload_folder", await server.upload_folder(case))

    if "error" in up:
        return
    step("list_storage new folder", await server.list_storage(folder_name))

    if args.no_launch:
        print("\n--no-launch: skipping simulation")
    else:
        fds = args.solver or pick_fds(solvers)
        if not fds:
            step("pick FDS version", {"error": "no FDS solver available"})
            return
        summary = step("launch (summary only)",
                       await server.launch_simulation(fds, 1, args.ram, folder_name))
        launched = step("launch (confirmed)",
                        await server.launch_simulation(fds, 1, args.ram, folder_name, confirm=True))
        sim_id = launched.get("simulation_id")
        if sim_id:
            step("get_simulation", await server.get_simulation(sim_id))
            final = step("wait_for_simulation",
                         await server.wait_for_simulation(sim_id, max_minutes=args.wait, poll_seconds=60))
            if not final.get("finished"):
                print(f"\nStill {final.get('status')} after {args.wait} min. "
                      f"Re-run later: get_simulation({sim_id})")
            else:
                status_ok = final.get("status") == "COMPLETED"
                step("simulation status is COMPLETED",
                     {"status": final.get("status")} if status_ok else
                     {"error": f"final status {final.get('status')}"})
                step("get_simulation with log", await server.get_simulation(sim_id, include_log=True))
                step("list_results", await server.list_results(folder_name))
                dl = step("download_results", await server.download_results(folder_name, "e2e_results"))
                outputs = [f for f in os.listdir("e2e_results") if f.endswith(("_devc.csv", "_hrr.csv"))] \
                    if os.path.isdir("e2e_results") else []
                step("FDS outputs present (_devc.csv/_hrr.csv)",
                     {"files": outputs} if outputs else
                     {"error": "no FDS output files: check e2e_results/mcp_e2e.log"})

    if args.cleanup:
        step("delete_storage (summary)", await server.delete_storage(folder_name))
        step("delete_storage (confirmed)", await server.delete_storage(folder_name, confirm=True))

    step("api_usage end", await server.api_usage())

    print("\n==================== SUMMARY")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nStorage folder used: {folder_name}")


if __name__ == "__main__":
    if not os.environ.get("CLOUDHPC_APIKEY"):
        sys.exit("export CLOUDHPC_APIKEY=<your API key> first")
    p = argparse.ArgumentParser()
    p.add_argument("--wait", type=int, default=20, help="minutes to wait for the run (max 30)")
    p.add_argument("--cleanup", action="store_true", help="delete the storage folder at the end")
    p.add_argument("--no-launch", action="store_true", help="upload only, do not run")
    p.add_argument("--solver", help="FDS script to use (default: newest available)")
    p.add_argument("--ram", default="standard", help="RAM type for the 1 vCPU run (default: standard)")
    p.add_argument("--confirm-costs", action="store_true",
                   help="confirm that a small billed simulation may be launched")
    asyncio.run(main(p.parse_args()))

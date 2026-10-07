"""Offline tests with a mocked cloudHPC API (response shapes taken from real API replies)."""

import io
import json
import os
import tarfile

import httpx
import pytest

os.environ["CLOUDHPC_MCP_MODE"] = "local"

from cloudhpc_mcp import advisor, files, server  # noqa: E402
from cloudhpc_mcp.client import CloudHPCClient, CloudHPCError  # noqa: E402

API = "https://api.test/api/v2"
RL = {"x-ratelimit-hourly-limit": "100", "x-ratelimit-hourly-used": "5",
      "x-ratelimit-daily-limit": "0", "x-ratelimit-daily-used": "5"}

SIM = {"id": 10030, "user_id": 4, "cpu": 48, "ram": "hypercore", "nopre": 0,
       "folder": "caseA", "mesh": "", "script": "openFoam-v2406", "clean": 0,
       "idate": "2026-09-24 09:31:20", "edate": "", "cpu_hrs": "", "cost": "",
       "status": 30, "logs": "", "images": [], "vnc_url": "https://vnc.example/x"}


def dir_item(i, name, parent=""):
    return {"id": i, "name": f"{parent}/{name}".strip("/"), "basename": name,
            "dirname": parent, "type": "dir", "size": ""}


def file_item(i, name, parent="", size=1234):
    return {"id": i, "name": f"{parent}/{name}".strip("/"), "basename": name,
            "dirname": parent, "type": "file", "size": size,
            "timeCreated": "2026-09-24T10:00:00Z"}


def make_tgz(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.uploaded = None
        self.upload_ct = None
        self.sim_status = 30
        self.result_blob = make_tgz({"out/result.txt": b"ok"})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.calls.append((m, path))

        if req.url.host == "signed.test":
            if m == "PUT":
                self.uploaded = req.read()
                self.upload_ct = req.headers.get("content-type")
                return httpx.Response(200)
            return httpx.Response(200, content=self.result_blob)

        if req.headers.get("x-api-key") != "good":
            return httpx.Response(403, json={"errors": ["Unauthorized."]})

        def ok(resp):
            return httpx.Response(200, json={"response": resp}, headers=RL)

        p = path.replace("/api/v2", "")
        if p == "/simulation/view-cpu":
            return ok([1, 2, 4, 8, 16, 32, 48, 64, 96, 112, 192, 224])
        if p == "/simulation/view-ram":
            return ok(["highcpu", "standard", "highmem", "hypercpu", "basegpu", "highcore", "hypercore"])
        if p == "/simulation/view-scripts":
            return ok(["fds6.9.1", "openFoam-v2406", "calculiX-2.21-PARDISO", "codeAster-17.0_mpi", "DAFoam-v5.0.0", "EnergyPlus-9.6.0", "EnergyPlus-25.2.0"])
        if p.startswith("/simulation/index-short/"):
            pg = int(p.rsplit("/", 1)[1])
            return ok([dict(SIM, status=self.sim_status), dict(SIM, id=10029, status=10)] if pg == 1 else [])
        if p.startswith("/simulation/view-short/") or p.startswith("/simulation/view/"):
            if p.endswith("/999"):
                return httpx.Response(403, json={"errors": ["A technical problem has occurred, try again later."]})
            return ok(dict(SIM, status=self.sim_status, logs="line1\nline2"))
        if p == "/simulation/add":
            self.added = json.loads(req.read())
            return ok(12345)
        if p.startswith("/simulation/stop/"):
            self.stopped = json.loads(req.read())
            return ok(True)
        if p.startswith("/simulation/sync/"):
            return ok(True)
        if p.startswith("/storage/index/name/asc/parent_id/"):
            pid, pg = p.split("/")[-2:]
            if pg != "1":
                return ok([])
            if pid == "1":
                return ok([dir_item(3, "sub", "caseA"), file_item(10, "upload.tar.gz", "caseA"),
                           file_item(11, "FDS.tar.gz", "caseA", 5_000_000),
                           file_item(12, "CloudHPC-massive-files.tar.gz", "caseA")])
            if pid == "3":
                return ok([file_item(20, "x.txt", "caseA/sub")])
            return ok([])
        if p.startswith("/storage/index/name/asc/"):
            pg = p.rsplit("/", 1)[1]
            return ok([dir_item(1, "caseA"), file_item(2, "loose.fds")] if pg == "1" else [])
        if p == "/storage/view-by-path":
            path_q = json.loads(req.read())["path"]
            return ok(file_item(11, os.path.basename(path_q), os.path.dirname(path_q)))
        if p.startswith("/storage/view-url/"):
            return ok({"mediaLink": "https://signed.test/download"})
        if p == "/storage/upload-url":
            self.upload_req = json.loads(req.read())
            return ok({"url": "https://signed.test/upload"})
        if p == "/user/delete-cache":
            return ok(True)
        if p.startswith("/storage/delete/"):
            return ok(True)
        return httpx.Response(404, json={"errors": ["not mocked"]})


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(server, "client_for",
                        lambda ctx=None: CloudHPCClient(api_key="good", api_url=API, transport=transport))
    return fake


# ---------------------------------------------------------------- client

async def test_bad_key_message():
    c = CloudHPCClient(api_key="bad", api_url=API, transport=httpx.MockTransport(FakeAPI()))
    with pytest.raises(CloudHPCError, match="invalid"):
        await c.cpu_options()


async def test_missing_key():
    with pytest.raises(CloudHPCError, match="No cloudHPC API key"):
        CloudHPCClient(api_key="  ", api_url=API)


async def test_rate_limit_tracking_and_block():
    c = CloudHPCClient(api_key="good", api_url=API, transport=httpx.MockTransport(FakeAPI()))
    await c.cpu_options()
    assert c.rate.as_dict()["hourly"]["remaining"] == 95
    assert "daily" not in c.rate.as_dict()
    c.rate.hourly_used = 100
    with pytest.raises(CloudHPCError, match="rate limit"):
        await c.cpu_options()


# ------------------------------------------------------------------ tools

async def test_list_solvers_grouped(api):
    r = await server.list_solvers()
    assert r["solvers"]["fds"] == ["fds6.9.1"]
    assert "openfoam" in r["solvers"]
    r = await server.list_solvers(search="PARDISO")
    assert r["solvers"] == {"calculix": ["calculiX-2.21-PARDISO"]}


async def test_list_storage_walks_tree(api):
    root = await server.list_storage()
    assert {i["name"] for i in root["items"]} == {"caseA", "loose.fds"}
    sub = await server.list_storage("caseA/sub")
    assert sub["items"][0]["path"] == "caseA/sub/x.txt"
    missing = await server.list_storage("nope")
    assert "not found" in missing["error"]


async def test_list_results_filters(api):
    r = await server.list_results("caseA")
    assert [x["name"] for x in r["results"]] == ["FDS.tar.gz"]


async def test_launch_requires_confirmation(api):
    r = await server.launch_simulation("fds6.9.1", 8, "highcpu", "caseA")
    assert r["confirmation_required"] and "fds6.9.1" in r["summary"]
    assert ("POST", "/api/v2/simulation/add") not in api.calls
    r = await server.launch_simulation("fds6.9.1", 8, "highcpu", "caseA", confirm=True)
    assert r["simulation_id"] == 12345
    assert api.added == {"cpu": 8, "ram": "highcpu", "script": "fds6.9.1", "folder": "caseA"}


async def test_launch_validation(api):
    r = await server.launch_simulation("openFoam-v2406", 7, "highcpu", "caseA", confirm=True)
    assert "problems" in r
    assert any("vCPU 7" in p for p in r["problems"])
    assert any("hyperthreading" in p for p in r["problems"])
    assert ("POST", "/api/v2/simulation/add") not in api.calls


async def test_launch_regular_and_mesh(api):
    await server.launch_simulation("openFoam-v2406", 32, "highcore", "caseA",
                                   mesh_folder="meshA", regular_instance=True, confirm=True)
    assert api.added["nopre"] == 1 and api.added["mesh"] == "meshA"


async def test_get_simulation_and_errors(api):
    r = await server.get_simulation(10030)
    assert r["status"] == "RUNNING" and r["solver"] == "openFoam-v2406"
    assert "vnc_url" not in r
    r = await server.get_simulation(10030, include_log=True)
    assert r["logs"].endswith("line2")
    r = await server.get_simulation(999)
    assert "technical problem" in r["error"]


async def test_list_simulations_filter(api):
    r = await server.list_simulations("active")
    assert [s["id"] for s in r["simulations"]] == [10030]
    r = await server.list_simulations("all")
    assert len(r["simulations"]) == 2


async def test_wait_returns_when_finished(api):
    api.sim_status = 10
    r = await server.wait_for_simulation(10030, max_minutes=1)
    assert r["finished"] and r["status"] == "COMPLETED"


async def test_stop_flow(api):
    r = await server.stop_simulation(10030)
    assert r["confirmation_required"]
    r = await server.stop_simulation(10030, mode="hard", confirm=True)
    assert r["stopping"] and api.stopped == {"signal": "SIGINT"}
    await server.stop_simulation(10030, confirm=True)
    assert api.stopped == {"signal": "SIGTSTP"}
    api.sim_status = 10
    r = await server.stop_simulation(10030, confirm=True)
    assert "not active" in r["error"]


async def test_delete_requires_confirmation(api):
    r = await server.delete_storage("caseA/FDS.tar.gz")
    assert r["confirmation_required"]
    assert not any(m == "DELETE" and "/storage/delete" in p for m, p in api.calls)
    r = await server.delete_storage("caseA/FDS.tar.gz", confirm=True)
    assert r["deleted"]


async def test_remote_desktop(api):
    r = await server.open_remote_desktop(10030)
    assert r["url"].startswith("https://vnc")
    api.sim_status = 10
    assert "error" in await server.open_remote_desktop(10030)


async def test_links(api):
    r = await server.get_download_link("caseA/FDS.tar.gz")
    assert r["url"] == "https://signed.test/download"
    r = await server.get_upload_link("caseB", "upload.tar.gz")
    assert "--upload-file" in r["curl"]
    assert api.upload_req == {"dirname": "caseB", "filename": "upload.tar.gz",
                              "contentType": "application/octet-stream"}


async def test_api_usage(api):
    from cloudhpc_mcp.client import REQUEST_LOG
    REQUEST_LOG.clear()
    await server.get_simulation(10030)
    await server.get_simulation(10029)
    r = await server.api_usage()
    assert r["rate_limits"]["hourly"]["limit"] == 100
    assert "daily" not in r["rate_limits"]      # daily limit 0 = no daily limit
    s = r["this_session"]
    assert s["api_requests"] == 3
    assert s["by_endpoint"]["GET /simulation/view-short/{n}"] == 2


# ------------------------------------------------------------- local tools

async def test_upload_folder_raw_targz(api, tmp_path):
    case = tmp_path / "myCase"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("x")
    (case / ".hidden").write_text("secret")
    r = await server.upload_folder(str(case))
    assert r["uploaded"] and r["storage_folder"] == "myCase"
    assert api.upload_req["dirname"] == "myCase"
    assert api.upload_ct == "application/octet-stream"
    with tarfile.open(fileobj=io.BytesIO(api.uploaded), mode="r:gz") as t:
        names = t.getnames()
    assert "system/controlDict" in names and ".hidden" not in names
    assert not any(n.startswith("myCase") for n in names)  # content at archive root


async def test_download_results_extracts(api, tmp_path):
    r = await server.download_results("caseA", str(tmp_path))
    assert r["downloads"][0]["file"] == "FDS.tar.gz"
    assert (tmp_path / "out" / "result.txt").read_text() == "ok"
    assert not (tmp_path / "FDS.tar.gz").exists()


async def test_download_rejects_path_traversal(api, tmp_path):
    api.result_blob = make_tgz({"../evil.txt": b"x"})
    r = await server.download_results("caseA", str(tmp_path / "d"))
    assert "Unsafe" in r["downloads"][0]["error"]
    assert not (tmp_path / "evil.txt").exists()


# ---------------------------------------------------------------- advisor

FDS_TEXT = """
&HEAD CHID='t' /
&MESH ID='m1', IJK=40,40,20, XB=0,4,0,4,0,2, MPI_PROCESS=0 /
&MESH ID='m2', IJK=40,40,20,
      XB=4,8,0,4,0,2, MPI_PROCESS=1 /
&MESH ID='m3', IJK=10,10,10, XB=8,9,0,1,0,1, MPI_PROCESS=1 /
&TAIL /
"""


def test_inspect_fds(tmp_path):
    (tmp_path / "case.fds").write_text(FDS_TEXT)
    info = advisor.inspect_case(str(tmp_path))
    f = info["fds"]
    assert info["family"] == "fds"
    assert f["meshes"] == 3 and f["mpi_groups"] == 2
    assert f["total_cells"] == 32000 + 32000 + 1000


def test_suggest_fds_rules(tmp_path):
    (tmp_path / "case.fds").write_text(FDS_TEXT)
    fds = advisor.inspect_case(str(tmp_path))["fds"]
    cpus = [1, 2, 4, 8, 16, 32]
    s = advisor.suggest("fds", cpus, [], fds=fds)
    assert s["ram"] == "highcpu" and s["cpu"] == 4  # 2 groups x 2
    s = advisor.suggest("fds", cpus, [], fds=fds, prefer_speed=True)
    assert s["ram"] == "hypercore" and s["cpu"] == 2  # 2 groups x 1


def test_suggest_fds_single_mesh_decomposition():
    fds = {"meshes": 1, "mpi_groups": 1, "total_cells": 1_000_000, "cells_per_group": [1_000_000],
           "uses_mpi_process": False, "uses_mult_id": False}
    s = advisor.suggest("fds", [1, 2, 4, 8, 16, 32, 48, 64, 96, 112], [], fds=fds)
    assert s["ideal_cpu"] == 66 * 2 and s["cpu"] == 112
    assert any("splits it" in n for n in s["notes"])


def test_suggest_openfoam():
    s = advisor.suggest("openfoam", [1, 2, 4, 8, 16, 32, 48, 64], [], cells=2_000_000)
    assert s["cpu"] == 32 and s["ram"] == "highcore"  # 40 ideal -> 32


def test_suggest_fea():
    s = advisor.suggest("calculix", [1, 2, 4, 8], [], nodes=865_000)
    assert s["cpu"] == 4 and s["ram"] == "highcpu"
    s = advisor.suggest("code_aster", [1, 2, 4, 8, 16, 32], [], nodes=865_000)
    assert s["cpu"] == 16  # 8 ranks x 2 threads


def test_inspect_openfoam_and_calculix(tmp_path):
    of = tmp_path / "of"
    (of / "system").mkdir(parents=True)
    (of / "system" / "controlDict").write_text("x")
    (of / "constant" / "polyMesh").mkdir(parents=True)
    (of / "constant" / "polyMesh" / "owner").write_text(
        'FoamFile { note "nPoints:10 nCells:123456 nFaces:5 nInternalFaces:3"; }')
    info = advisor.inspect_case(str(of))
    assert info["cells"] == 123456
    assert any("decomposeParDict" in n for n in info["notes"])

    cx = tmp_path / "cx"
    cx.mkdir()
    (cx / "beam.inp").write_text("** c\n*NODE, NSET=all\n1,0,0,0\n2,1,0,0\n*ELEMENT, TYPE=C3D4\n1,1,2,3,4\n")
    assert advisor.inspect_case(str(cx))["nodes"] == 2


def test_family_of():
    assert advisor.family_of("snappyHexMesh-v2312") == "openfoam"
    assert advisor.family_of("codeAster-17.0_mpi") == "code_aster"
    assert advisor.family_of("OpenRadioss") == "openradioss"
    assert advisor.family_of("ubuntu-2404-static") == "other"


def test_result_file_filter():
    assert files.is_result_file("OPENFOAM-solution.tar.gz")
    assert files.is_result_file("case.rmed")
    assert not files.is_result_file("upload.tar.gz")
    assert not files.is_result_file("CloudHPC-massive-files.tar.gz")
    assert not files.is_result_file("CloudHPC-massive-files-20260925000914.tar.gz")


def test_single_highcpu_upgraded_to_standard():
    fds = {"meshes": 1, "mpi_groups": 1, "total_cells": 8000, "cells_per_group": [8000],
           "uses_mpi_process": False, "uses_mult_id": False}
    s = advisor.suggest("fds", [1, 2, 4], [], fds=fds)
    assert s["cpu"] == 2 and s["ram"] == "highcpu"   # 1 mesh x 2 (hyperthreading)
    s = advisor.suggest("fds", [1, 4], [], fds=fds)
    assert s["cpu"] == 4 and s["ram"] == "highcpu"   # rounded up, not 1 vCPU
    s = advisor.suggest("calculix", [1, 2, 4], [], nodes=10_000)
    assert s["cpu"] == 1 and s["ram"] == "standard"


async def test_launch_refuses_single_highcpu(api):
    r = await server.launch_simulation("fds6.9.1", 1, "highcpu", "caseA", confirm=True)
    assert any("1 vCPU highcpu" in p for p in r["problems"])
    r = await server.launch_simulation("fds6.9.1", 1, "standard", "caseA")
    assert r["confirmation_required"]


def test_ram_ladder_and_memory_detection():
    assert advisor.next_ram("highcpu") == "standard"
    assert advisor.next_ram("standard") == "highmem"
    assert advisor.next_ram("highmem") is None
    log = "MPIDU_Init_shm_alloc(139): Unable to allocate -2098937792 bytes of memory for segment (probably out of memory)"
    assert advisor.looks_like_memory_error(log)
    assert not advisor.looks_like_memory_error("STOP: FDS completed successfully")


async def test_get_simulation_memory_hint(api, monkeypatch):
    orig = api.__call__
    def patched(req):
        if "/simulation/view/" in req.url.path:
            import httpx as h
            return h.Response(200, json={"response": dict(SIM, ram="highcpu", status=60,
                              output="Unable to allocate 123 bytes (probably out of memory)")})
        return orig(req)
    monkeypatch.setattr(server, "client_for", lambda ctx=None: CloudHPCClient(
        api_key="good", api_url=API, transport=httpx.MockTransport(patched)))
    r = await server.get_simulation(10030, include_log=True)
    assert r["memory_error"] and "standard" in r["suggestion"]


# ------------------------------------------------------------ error catalogue

from cloudhpc_mcp import errors, guides  # noqa: E402


def test_diagnose_catalogue():
    out = ("@@@ RAM used > 80.0%: increase vCPU or use highmem instance\n"
           "=   KILLED BY SIGNAL: 9 (Killed)\n"
           "@@@ ERROR: low vCPU selected\n"
           "@@@ WARNING: high number of Pressure Zones found - risk of poor scalability\n")
    ids = [d["id"] for d in errors.diagnose(out)]
    assert ids[:2] == ["ram_out", "fds_low_vcpu"] or set(ids[:2]) == {"ram_out", "fds_low_vcpu"}
    assert "ram_high" in ids and "fds_pressure_zones" in ids
    assert all(d["docs"].startswith("https://docs.cloudhpc.cloud/errors/#") for d in errors.diagnose(out))
    assert errors.diagnose("STOP: FDS completed successfully") == []
    assert errors.diagnose("@@@ ERROR: openFoam script runs with nProc > 1")[0]["id"] == "of_nproc"


async def test_wait_adds_diagnosis_on_completed_with_error(api, monkeypatch):
    orig = api.__call__

    def patched(req):
        if "/simulation/view/" in req.url.path:
            return httpx.Response(200, json={"response": dict(
                SIM, script="fds6.9.1", ram="highcpu", status=10,
                output="@@@ ERROR: MPI_MPI_PROCESS incorrect\n")})
        if "/simulation/view-short/" in req.url.path:
            return httpx.Response(200, json={"response": dict(SIM, status=10)})
        return orig(req)

    monkeypatch.setattr(server, "client_for", lambda ctx=None: CloudHPCClient(
        api_key="good", api_url=API, transport=httpx.MockTransport(patched)))
    r = await server.wait_for_simulation(10030, max_minutes=1)
    assert r["finished"] and r["status"] == "COMPLETED"
    assert r["diagnosis"][0]["id"] == "fds_mpi_order"
    assert "COMPLETED but" in r["warning"]


def test_preflight_fds_and_names(tmp_path):
    case = tmp_path / "bad(name)"
    case.mkdir()
    (case / "c.fds").write_text(
        "&MESH IJK=10,10,10, XB=0,1,0,1,0,1, MPI_PROCESS=1 /\n"
        "&MESH IJK=10,10,10, XB=1,2,0,1,0,1, MPI_PROCESS=0 /\n"
        "&DEVC ID='v', QUANTITY='VISIBILITY', XYZ=1,1,1 /\n")
    info = advisor.inspect_case(str(case))
    issues = errors.preflight(info)
    text = " ".join(i["issue"] for i in issues)
    assert "characters not accepted" in text
    assert "ascending order" in text
    assert "AMD" in text


def test_preflight_openfoam(tmp_path):
    of = tmp_path / "of"
    (of / "system").mkdir(parents=True)
    (of / "system" / "controlDict").write_text("startFrom startTime;\n")
    (of / "system" / "decomposeParDict").write_text("method simple;\n")
    issues = errors.preflight(advisor.inspect_case(str(of)))
    text = " ".join(i["issue"] for i in issues)
    assert "scotch or hierarchical" in text and "polyMesh" in text and "latestTime" in text


async def test_launch_name_and_openfoam_min(api):
    r = await server.launch_simulation("fds6.9.1", 4, "highcpu", "my case(1)", confirm=True)
    assert any("characters" in p for p in r["problems"])
    r = await server.launch_simulation("openFoam-v2406", 1, "highcore", "caseA", confirm=True)
    assert any("at least 2 vCPU" in p for p in r["problems"])


def test_fds_rounds_up_to_cover_meshes():
    fds = {"meshes": 3, "mpi_groups": 3, "total_cells": 90_000, "cells_per_group": [30_000] * 3,
           "uses_mpi_process": False, "uses_mult_id": False}
    s = advisor.suggest("fds", [1, 2, 4, 8, 16], [], fds=fds)
    assert s["cpu"] == 8  # 3 meshes x 2 = 6 -> round up to 8, never 4


def test_openfoam_minimum_two():
    s = advisor.suggest("openfoam", [1, 2, 4, 8], [], cells=30_000)
    assert s["cpu"] == 2 and s["ram"] == "highcore"


def test_fds_success_marker():
    ok = server._diagnosis({"script": "fds6.11.1", "status": 10,
                            "output": " Starting FDS ...\nSTOP: FDS completed successfully (CHID: x)"})
    assert ok["solver_finished_ok"] is True and "warning" not in ok
    bad = server._diagnosis({"script": "fds6.11.1", "status": 10,
                             "output": " Starting FDS ...\n Time Step: 5"})
    assert bad["solver_finished_ok"] is False and "did not print" in bad["warning"]
    assert "solver_finished_ok" not in server._diagnosis({"script": "fds6.11.1", "status": 10, "output": ""})


def test_devc_note_only_when_present(tmp_path):
    base = "&MESH IJK=30,30,30, XB=0,1,0,1,0,1 /\n&MESH IJK=30,30,30, XB=1,2,0,1,0,1 /\n"
    (tmp_path / "a.fds").write_text(base)
    fds = advisor.inspect_case(str(tmp_path))["fds"]
    s = advisor.suggest("fds", [1, 2, 4, 8], [], fds=fds)
    assert not any("DEVC" in n for n in s["notes"])
    (tmp_path / "a.fds").write_text(base + "&DEVC ID='v', QUANTITY='VISIBILITY', XYZ=1,1,1 /\n")
    fds = advisor.inspect_case(str(tmp_path))["fds"]
    assert fds["slow_devc_on_amd"] == ["VISIBILITY"]
    s = advisor.suggest("fds", [1, 2, 4, 8], [], fds=fds)
    assert any("VISIBILITY" in n for n in s["notes"])


def test_cost_in_euro():
    assert server._eur("0.027") == "€0.027"
    assert server._eur(12.5) == "€12.50"
    assert server._eur("") is None
    assert server._sim_summary({"cost": "1.2"})["cost_eur"] == "€1.20"


def test_check_solver_logs(tmp_path):
    (tmp_path / "log.blockMesh").write_text("Creating block mesh\nEnd\n")
    (tmp_path / "log.foamRun").write_text("Time = 10\n--> FOAM FATAL ERROR: bad\n")
    (tmp_path / "log.decomposePar").write_text("Processor 0\n")
    (tmp_path / "room.out").write_text(" Fire Dynamics Simulator\nSTOP: FDS completed successfully (CHID: room)\n")
    res = {c["file"]: c["status"] for c in errors.check_solver_logs(str(tmp_path))}
    assert res == {"log.blockMesh": "ok", "log.foamRun": "error",
                   "log.decomposePar": "warning", "room.out": "ok"}


async def test_download_reports_solver_logs(api, tmp_path):
    api.result_blob = make_tgz({"log.simpleFoam": b"Time = 1\nEnd\n"})
    r = await server.download_results("caseA", str(tmp_path))
    assert r["solver_ok"] is True and r["solver_logs"][0]["file"] == "log.simpleFoam"


async def test_download_does_not_overwrite_inputs(api, tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "decomposeParDict").write_text("original")
    api.result_blob = make_tgz({"system/decomposeParDict": b"changed", "0.3/U": b"u"})
    r = await server.download_results("caseA", str(tmp_path))
    assert (tmp_path / "system" / "decomposeParDict").read_text() == "original"
    assert (tmp_path / "0.3" / "U").read_text() == "u"
    assert r["downloads"][0]["kept_existing"] == ["system/decomposeParDict"]
    api.result_blob = make_tgz({"system/decomposeParDict": b"changed"})
    await server.download_results("caseA", str(tmp_path), overwrite=True)
    assert (tmp_path / "system" / "decomposeParDict").read_text() == "changed"


async def test_upload_blocked_by_preflight(api, tmp_path):
    case = tmp_path / "ofcase"
    (case / "system").mkdir(parents=True)
    (case / "system" / "controlDict").write_text("startFrom latestTime;\n")
    (case / "system" / "decomposeParDict").write_text("method simple;\n")
    r = await server.upload_folder(str(case))
    assert "nothing was uploaded" in r["error"]
    assert any("scotch or hierarchical" in p["issue"] for p in r["problems"])
    assert api.uploaded is None
    r = await server.upload_folder(str(case), ignore_preflight=True)
    assert r["uploaded"]


async def test_upload_bad_local_name_ok_with_clean_storage_name(api, tmp_path):
    case = tmp_path / "my case (v2)"
    case.mkdir()
    (case / "c.fds").write_text("&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n")
    r = await server.upload_folder(str(case))
    assert "error" in r
    r = await server.upload_folder(str(case), storage_folder="my_case_v2")
    assert r["uploaded"] and r["storage_folder"] == "my_case_v2"


def test_preflight_allrun_logs_and_leftovers(tmp_path):
    of = tmp_path / "of"
    (of / "system").mkdir(parents=True)
    (of / "system" / "controlDict").write_text("startFrom latestTime;\n")
    (of / "Allrun").write_text("runApplication blockMesh\n")
    (of / "log.blockMesh").write_text("End\n")
    (of / "cpu.csv").write_text("x")
    text = " ".join(i["issue"] for i in errors.preflight(advisor.inspect_case(str(of))))
    assert "skips every step" in text and "previous cloudHPC run" in text


def test_preflight_mpi_process_gaps_and_mixed(tmp_path):
    (tmp_path / "a.fds").write_text(
        "&MESH IJK=30,30,30, XB=0,1,0,1,0,1, MPI_PROCESS=1 /\n"
        "&MESH IJK=30,30,30, XB=1,2,0,1,0,1, MPI_PROCESS=1 /\n")
    text = " ".join(i["issue"] for i in errors.preflight(advisor.inspect_case(str(tmp_path))))
    assert "start at 0" in text
    (tmp_path / "a.fds").write_text(
        "&MESH IJK=30,30,30, XB=0,1,0,1,0,1, MPI_PROCESS=0 /\n"
        "&MESH IJK=30,30,30, XB=1,2,0,1,0,1 /\n")
    text = " ".join(i["issue"] for i in errors.preflight(advisor.inspect_case(str(tmp_path))))
    assert "every mesh or on none" in text


async def test_launch_checks_fds_cores_after_upload(api, tmp_path):
    case = tmp_path / "room"
    case.mkdir()
    (case / "r.fds").write_text(
        "&MESH IJK=30,30,30, XB=0,1,0,1,0,1, MPI_PROCESS=0 /\n"
        "&MESH IJK=30,30,30, XB=1,2,0,1,0,1, MPI_PROCESS=1 /\n")
    assert (await server.upload_folder(str(case)))["uploaded"]
    r = await server.launch_simulation("fds6.9.1", 1, "standard", "room", confirm=True)
    assert any("low vCPU selected" in p for p in r["problems"])
    # 2 processes on 1 physical core: allowed, with a speed note in the summary
    r = await server.launch_simulation("fds6.9.1", 2, "standard", "room")
    assert r["confirmation_required"] and "share each core" in r["summary"]
    r = await server.launch_simulation("fds6.9.1", 2, "highcore", "room")
    assert r["confirmation_required"]
    r = await server.launch_simulation("fds6.9.1", 4, "highcpu", "room")
    assert r["confirmation_required"]


class _FakeCtx:
    """Minimal context: a client that supports elicitation and answers `answer`."""
    def __init__(self, answer, supported=True):
        self.answer, self.supported, self.asked = answer, supported, []

    @property
    def client_capabilities(self):
        class C:
            elicitation = {} if self.supported else None
        return C()

    async def elicit(self, message, schema):
        self.asked.append(message)
        class R:
            action = "accept" if self.answer is not None else "cancel"
            data = schema(confirm=bool(self.answer)) if self.answer is not None else None
        return R()


async def test_delete_uses_elicitation(api):
    ctx = _FakeCtx(answer=False)
    r = await server.delete_storage("caseA/FDS.tar.gz", confirm=True, ctx=ctx)   # confirm ignored
    assert r["cancelled"] and ctx.asked
    assert not any(m == "DELETE" and "/storage/delete" in p for m, p in api.calls)
    ctx = _FakeCtx(answer=True)
    r = await server.delete_storage("caseA/FDS.tar.gz", ctx=ctx)                 # no confirm needed
    assert r["deleted"]


async def test_launch_and_hard_stop_use_elicitation(api):
    r = await server.launch_simulation("fds6.9.1", 8, "highcpu", "caseA", confirm=True,
                                       ctx=_FakeCtx(answer=None))                # dialog cancelled
    assert r["cancelled"]
    r = await server.stop_simulation(10030, mode="hard", ctx=_FakeCtx(answer=True))
    assert r["stopping"] and api.stopped == {"signal": "SIGINT"}


async def test_fallback_without_elicitation(api):
    r = await server.delete_storage("caseA/FDS.tar.gz", ctx=_FakeCtx(answer=True, supported=False))
    assert r["confirmation_required"]


async def test_missing_files_hint(api):
    r = await server.list_results("nope")
    assert "not found" in r["error"] and "60 days" in r["hint"]
    r = await server.list_storage("nope/sub")
    assert "60 days" in r["hint"]


# ------------------------------------------------------------ other solvers

def _case(tmp_path, name, files_):
    d = tmp_path / name
    d.mkdir()
    for fn, content in files_.items():
        p = d / fn
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


def test_family_of_new_solvers():
    assert advisor.family_of("DAFoam-v5.0.0") == "dafoam"
    assert advisor.family_of("DAFoam-turbo") == "dafoam"
    assert advisor.family_of("CONTAM-3.4.0") == "contam"
    assert advisor.family_of("EnergyPlus-9.6.0") == "energyplus"
    assert advisor.family_of("LIGGGHTS-3.8.0") == "liggghts"
    assert advisor.family_of("openlb-1.8r1") == "openlb"
    assert advisor.family_of("openTELEMAC-v8p5r1") == "telemac"
    assert advisor.family_of("openFoam-v2406") == "openfoam"


def test_detect_and_preflight_new_solvers(tmp_path):
    pf = lambda d: " ".join(i["issue"] for i in errors.preflight(advisor.inspect_case(str(d))))

    d = _case(tmp_path, "cont", {"a.prj": "x", "b.prj": "y"})
    assert advisor.inspect_case(str(d))["family"] == "contam"
    assert "only the first" in pf(d)

    d = _case(tmp_path, "eplus", {"house.idf": "x"})
    assert advisor.inspect_case(str(d))["family"] == "energyplus"
    assert "weather" in pf(d)

    d = _case(tmp_path, "tel", {"t3d.cas": "x", "geo.prj": "gis"})
    assert advisor.inspect_case(str(d))["family"] == "telemac"
    assert "TELEMAC-3D" in pf(d)

    d = _case(tmp_path, "lig", {"in.hopper": "x", "inlet.stl": "y"})
    assert advisor.inspect_case(str(d))["family"] == "liggghts"
    assert "Several files start with 'in'" in pf(d)

    d = _case(tmp_path, "olb", {"Makefile": "OLB_ROOT := x", "cavity.cpp": "int main(){}", "run.sh": "x"})
    os.chmod(d / "run.sh", 0o755)
    assert advisor.inspect_case(str(d))["family"] == "openlb"
    assert "Executable files" in pf(d)

    d = _case(tmp_path, "rad", {"crash_0000.rad": "x"})
    assert advisor.inspect_case(str(d))["family"] == "openradioss"
    assert "no engine file" in pf(d)
    d = _case(tmp_path, "rad2", {"crash_0000.rad": "x", "crash_0001.rad": "y", "model.key": "z"})
    assert ".key file (model.key) is used" in pf(d)

    d = _case(tmp_path, "dafoam", {"runScript.py": "x", "Allrun": "y", "system/controlDict": "c",
                                   "system/decomposeParDict": "numberOfSubdomains 4;\n"})
    info = advisor.inspect_case(str(d))
    assert info["family"] == "dafoam" and info["number_of_subdomains"] == 4
    assert "runs BOTH" in pf(d)


def test_suggest_new_solvers():
    cpus = [1, 2, 4, 8, 16, 32]
    assert advisor.suggest("contam", cpus, [])["cpu"] == 2
    assert advisor.suggest("liggghts", cpus, [])["ram"] in ("highcore", "hypercore")
    s = advisor.suggest("dafoam", cpus, [], cells=400_000)
    assert s["cpu"] == 8 and s["ram"] == "highcore"
    assert "openradioss" not in advisor.NO_HYPERTHREAD


async def test_solver_guide_tool():
    g = await server.solver_guide("EnergyPlus-9.6.0")
    assert g["family"] == "energyplus" and any(".epw" in x for x in g["input"])
    g = await server.solver_guide("dafoam")
    assert any("NOT adjusted" in x for x in g["automatic"])
    assert "error" in await server.solver_guide("unknownsolver")
    # the guides must not expose platform internals
    text = json.dumps(guides.GUIDES)
    for leak in ("/opt/", "/home/", "/usr/", "mpirun", "mpiexec", "sed -i", "lscpu",
                 "rm -rf", "foamDictionary", "runApplication", "runParallel", "OMP_NUM_THREADS",
                 "nproc", "grep ", "ulimit", "#!/bin"):
        assert leak not in text, leak


async def test_solver_guides_from_scripts():
    g = await server.solver_guide("fds6.9.1")
    text = json.dumps(g)
    assert ".stop" in text and "RESTART=.TRUE." in text and "DT_RESTART" in text
    for name, fam in (("codeSaturne-9.0.1", "codesaturne"), ("SWAN-41.51", "swan"),
                      ("XBeach_mpi", "xbeach"), ("custom-script-u24", "custom"),
                      ("SU2_CFD8.3.0", "su2"), ("openFoam-v2406", "openfoam"),
                      ("snappyHexMesh-v2412", "openfoam"), ("calculiX-2.21", "calculix"),
                      ("codeAster-17.0_mpi", "code_aster")):
        g = await server.solver_guide(name)
        assert g["family"] == fam, name
    g = await server.solver_guide("openfoam")
    assert any("fvSchemes-transient" in o for o in g["options"])


def test_fds_preflight_from_scripts(tmp_path):
    (tmp_path / "room.fds").write_bytes(
        "&HEAD CHID='room' TITLE='Stanza è' /\n&TIME T_END=60. /\n"
        "&MESH IJK=60,60,30, XB=0,6,0,6,0,3 /\n&VENT XB=0,0,0,6,0,3, MB='XMIN' /\n"
        "&RAMP ID='r', T=120., F=1. /\n&PART ID='p' /\n&TAIL /\n".encode("utf-8"))
    (tmp_path / "hrr.csv").write_text("t,q\n")
    info = advisor.inspect_case(str(tmp_path))
    assert info["fds"]["non_ascii"] and info["fds"]["mesh_boundary_vents"]
    assert not advisor.fds_auto_split(info["fds"])
    text = " ".join(i["issue"] for i in errors.preflight(info))
    for part in ("non-ASCII", "T_END", "hrr.csv", "&PART", "MB="):
        assert part in text, part
    (tmp_path / "room.restart").write_text("x")   # restart: nothing is removed
    text = " ".join(i["issue"] for i in errors.preflight(advisor.inspect_case(str(tmp_path))))
    assert "hrr.csv" not in text


def test_fds_auto_split_rules():
    base = {"meshes": 1, "uses_mult_id": False, "uses_mpi_process": False,
            "mesh_boundary_vents": False, "total_cells": 100_000}
    assert advisor.fds_auto_split(base)
    assert not advisor.fds_auto_split({**base, "total_cells": 20_000})
    assert not advisor.fds_auto_split({**base, "uses_mult_id": True})
    assert not advisor.fds_auto_split({**base, "meshes": 2})


def test_new_family_detection_and_preflight(tmp_path):
    sat = tmp_path / "sat"
    (sat / "case1" / "DATA").mkdir(parents=True)
    (sat / "case1" / "SRC").mkdir()
    info = advisor.inspect_case(str(sat))
    assert info["family"] == "codesaturne" and info["saturne_case"] == "case1"
    sw = tmp_path / "sw"
    sw.mkdir()
    (sw / "a.swn").write_text("x")
    (sw / "b.swn").write_text("x")
    info = advisor.inspect_case(str(sw))
    assert info["family"] == "swan"
    assert any("only the first" in i["issue"] for i in errors.preflight(info))
    xb = tmp_path / "xb"
    xb.mkdir()
    (xb / "params.txt").write_text("x")
    assert advisor.inspect_case(str(xb))["family"] == "xbeach"
    cu = tmp_path / "cu"
    cu.mkdir()
    (cu / "run.py").write_text("print(1)")
    info = advisor.inspect_case(str(cu))
    assert info["family"] == "custom"
    assert any("requirements.txt" in i["issue"] for i in errors.preflight(info))
    ccx = tmp_path / "ccx"
    ccx.mkdir()
    (ccx / "a_mesh.inp").write_text("*NODE\n1,0,0,0\n")
    (ccx / "model.inp").write_text("*INCLUDE,INPUT=a_mesh.inp\n")
    info = advisor.inspect_case(str(ccx))
    assert any("*INCLUDE" in i["issue"] for i in errors.preflight(info))


def test_code_aster_preflight_from_scripts(tmp_path):
    (tmp_path / "study.export").write_text(
        "P mpi_nbcpu 4\nF comm C:\\Users\\me\\study.comm D 1\nF mmed /home/me/mesh.med D 20\n")
    (tmp_path / "study.comm").write_text("DEBUT()\nFIN()\n")
    info = advisor.inspect_case(str(tmp_path))
    assert info["export_inputs"] == ["mesh.med", "study.comm"] and not info["comm_parallel"]
    text = " ".join(i["issue"] for i in errors.preflight(info))
    assert "single MPI process" in text and "mesh.med" in text


async def test_launch_code_aster_needs_two_vcpu_per_process(api, tmp_path):
    d = tmp_path / "ca"
    d.mkdir()
    (d / "s.export").write_text("P mpi_nbcpu 4\nF comm s.comm D 1\nF mmed m.med D 20\n")
    (d / "s.comm").write_text("MODI_MODELE(PARTITIONNEUR='METIS', NB_SOUS_DOMAINE=4)\n")
    (d / "m.med").write_text("x")
    assert (await server.upload_folder(str(d)))["uploaded"]
    r = await server.launch_simulation("codeAster-17.0_mpi", 4, "highcpu", "ca", confirm=True)
    assert any("at least 8 vCPU" in p for p in r["problems"])


def test_diagnose_script_messages():
    ids = {d["id"] for d in errors.diagnose(
        "@@@ WARNING: FDS file with non ASCII characters - possible improper behaviour\n"
        "@@@ ERROR: application not set in system/controlDict\n"
        "@@@ ERROR: mesh script runs with nProc > 1\n"
        "No parallelism detected ...\n")}
    assert {"fds_non_ascii", "of_application", "of_nproc", "ca_serial"} <= ids


async def test_launch_checks_dafoam_subdomains(api, tmp_path):
    d = _case(tmp_path, "daf", {"runScript.py": "x", "preProcessing.sh": "y",
                                "system/controlDict": "c",
                                "system/decomposeParDict": "numberOfSubdomains 4;\n"})
    assert (await server.upload_folder(str(d)))["uploaded"]
    r = await server.launch_simulation("DAFoam-v5.0.0", 8, "highcore", "daf", confirm=True)
    assert any("numberOfSubdomains is 4" in p for p in r["problems"])
    r = await server.launch_simulation("DAFoam-v5.0.0", 4, "highcore", "daf")
    assert r["confirmation_required"]
    r = await server.launch_simulation("DAFoam-v5.0.0", 8, "highcpu", "daf", confirm=True)
    assert any("hyperthreading" in p for p in r["problems"])


async def test_energyplus_version_and_templates(api, tmp_path):
    d = _case(tmp_path, "house", {"house.idf": "Version,\n  9.6;\nBuilding, x;\n", "w.epw": "x"})
    info = advisor.inspect_case(str(d))
    assert info["idf_version"] == "9.6" and not info["hvac_templates"]
    assert (await server.upload_folder(str(d)))["uploaded"]
    r = await server.launch_simulation("EnergyPlus-25.2.0", 2, "highcpu", "house", confirm=True)
    assert any("version 9.6" in p for p in r["problems"])
    r = await server.launch_simulation("EnergyPlus-9.6.0", 2, "highcpu", "house")
    assert r["confirmation_required"]

    d = _case(tmp_path, "hvac", {"m.idf": "Version, 25.2;\nHVACTemplate:Zone:IdealLoadsAirSystem, z;\n"})
    r = await server.upload_folder(str(d))
    assert any("HVACTemplate" in p["issue"] for p in r["problems"])
    assert advisor.family_of("foamExtend-5.0") == "openfoam"


# ------------------------------------------------------------- public mode

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

def test_public_mode_exposes_only_guide_tools():
    import subprocess
    import sys
    code = ("import asyncio; from cloudhpc_mcp import server; "
            "print(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))); "
            "print('API keys' in server.mcp.instructions)")
    env = {**os.environ, "CLOUDHPC_MCP_MODE": "public", "PYTHONPATH": SRC}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True).stdout.splitlines()
    assert out[0] == str(sorted(server.PUBLIC_TOOLS))
    assert out[1] == "True"


def test_invalid_mode_is_rejected():
    import subprocess
    import sys
    env = {**os.environ, "CLOUDHPC_MCP_MODE": "publik", "PYTHONPATH": SRC}
    r = subprocess.run([sys.executable, "-c", "import cloudhpc_mcp.server"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "must be local, remote or public" in r.stderr


async def test_public_catalog_is_cached(api, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC", True)
    monkeypatch.setattr(server, "_CATALOG", {})
    await server.list_solvers()
    await server.list_machine_options()
    await server.suggest_resources("fds", fds_meshes=2)
    await server.list_solvers(search="openfoam")
    catalog_calls = [c for c in api.calls if "view-" in c[1]]
    assert len(catalog_calls) == 3  # scripts, cpu, ram once each


async def test_public_catalog_serves_stale_on_api_error(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC", True)
    monkeypatch.setattr(server, "_CATALOG", {"scripts": (0.0, ["fds6.9.1"])})  # expired
    down = httpx.MockTransport(lambda req: httpx.Response(503, json={"errors": ["down"]}))
    monkeypatch.setattr(server, "client_for", lambda ctx=None: CloudHPCClient(
        api_key="k", api_url=API, transport=down))
    r = await server.list_solvers()
    assert "error" not in r and "fds6.9.1" in json.dumps(r)


def test_client_ip_uses_rightmost_forwarded_entry():
    scope = {"headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7")],
             "client": ("169.254.1.1", 1)}
    assert server.client_ip(scope) == "203.0.113.7"          # 6.6.6.6 is forgeable
    assert server.client_ip(scope, trusted_proxies=1) == "6.6.6.6"
    assert server.client_ip({"headers": [], "client": ("10.0.0.1", 5)}) == "10.0.0.1"


async def test_rate_limit_per_ip():
    now = [1000.0]
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = server.PerIPRateLimit(app, per_minute=3, clock=lambda: now[0])

    async def hit(ip):
        sent = []

        async def send(m):
            sent.append(m)

        await mw({"type": "http", "path": "/mcp", "headers": [(b"x-forwarded-for", ip.encode())]},
                 None, send)
        return sent[0]["status"]

    assert [await hit("1.1.1.1") for _ in range(4)] == [200, 200, 200, 429]
    assert await hit("2.2.2.2") == 200          # other clients unaffected
    now[0] += 61
    assert await hit("1.1.1.1") == 200          # window passed


async def test_catalog_error_has_no_storage_hint(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC", True)
    monkeypatch.setattr(server, "_CATALOG", {})
    bad = httpx.MockTransport(lambda req: httpx.Response(
        403, json={"errors": ["A technical problem has occurred, try again later."]}))
    monkeypatch.setattr(server, "client_for", lambda ctx=None: CloudHPCClient(
        api_key="k", api_url=API, transport=bad))
    for r in (await server.list_solvers(), await server.list_machine_options(),
              await server.suggest_resources("fds", fds_meshes=2)):
        assert "error" in r and "hint" not in r and "solver_guide" in r["note"]


async def test_openfoam_guide_explains_physical_cores():
    g = await server.solver_guide("openFoam-v2406")
    assert "vCPU / 2" in g["cores"] and "32 vCPU on standard = 16" in g["cores"]
    assert any("16 physical cores" in f["a"] for f in g["faq"])
    assert "cores" not in await server.solver_guide("fds")
    assert "vCPU / 2" in server.PUBLIC_INSTRUCTIONS and "16 processes" in server.INSTRUCTIONS

"""Case inspection and vCPU/RAM advice based on the cloudHPC scalability rules.

Rules (docs.cloudhpc.cloud/scalability + cloudHPC blog posts):
  FDS        vCPU = MPI groups x2 on highcpu/standard/highmem/hypercpu,
             x1 on highcore/hypercore; >= 15,000-20,000 cells per mesh/group;
             a single &MESH is split automatically across the physical cores
             (>= 15,000 cells and >= 12 cells per direction per piece).
  OpenFOAM   MPI only -> highcore/hypercore; >= 50,000 cells per core.
  CalculiX   PARDISO scales up to ~200,000 nodes per core.
  code_aster MPI ranks with >= 100,000 nodes per rank, 2 threads per rank (max 4).
  OpenRadioss >= 20,000-30,000 elements per core, physical cores (hypercore).
  RAM        hyperthread-capable solvers: start on highcpu, then standard,
             then highmem if the run fails for memory. Solvers without
             hyperthreading (OpenFOAM & co.) use highcore or hypercore.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

# ----------------------------------------------------------------- families

FAMILIES = {
    "fds": "FDS (Fire Dynamics Simulator)",
    "openfoam": "OpenFOAM (incl. snappyHexMesh, cfMesh, foam-extend)",
    "calculix": "CalculiX",
    "code_aster": "code_aster",
    "openradioss": "OpenRadioss",
    "su2": "SU2",
    "dafoam": "DAFoam",
    "contam": "CONTAM",
    "energyplus": "EnergyPlus",
    "liggghts": "LIGGGHTS",
    "openlb": "OpenLB",
    "telemac": "openTELEMAC",
    "codesaturne": "code_saturne",
    "swan": "SWAN",
    "xbeach": "XBeach",
    "custom": "Custom scripts (bash / Python)",
    "other": "Other / generic",
}

# solvers that only use physical cores (MPI): no hyperthreading
# (OpenRadioss uses OpenMP threads on the hyperthreads, so it is not listed)
NO_HYPERTHREAD = {"openfoam", "su2", "dafoam", "liggghts", "telemac", "codesaturne", "xbeach"}

HT_RAM_LADDER = ["highcpu", "standard", "highmem"]
PHYSICAL_RAM = ["highcore", "hypercore"]


def family_of(script: str) -> str:
    s = script.lower()
    if "dafoam" in s:
        return "dafoam"
    for prefix, fam in (("contam", "contam"), ("energyplus", "energyplus"),
                        ("liggghts", "liggghts"), ("openlb", "openlb"),
                        ("opentelemac", "telemac"), ("telemac", "telemac"),
                        ("codesaturne", "codesaturne"), ("code_saturne", "codesaturne"),
                        ("swan", "swan"), ("xbeach", "xbeach"), ("custom-script", "custom")):
        if s.startswith(prefix):
            return fam
    if s.startswith("fds"):
        return "fds"
    if any(k in s for k in ("openfoam", "snappyhexmesh", "cfmesh", "foam-extend", "foamextend")):
        return "openfoam"
    if s.startswith("calculix"):
        return "calculix"
    if s.startswith("codeaster") or s.startswith("code_aster"):
        return "code_aster"
    if "radioss" in s:
        return "openradioss"
    if "su2" in s:
        return "su2"
    return "other"


def group_scripts(scripts: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for s in scripts:
        groups.setdefault(family_of(s), []).append(s)
    return groups


# --------------------------------------------------------------- inspection

_MESH_RE = re.compile(r"&MESH\b(.*?)/", re.IGNORECASE | re.DOTALL)
_IJK_RE = re.compile(r"\bIJK\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_MPI_RE = re.compile(r"\bMPI_PROCESS\s*=\s*(\d+)", re.IGNORECASE)
_MULT_RE = re.compile(r"\bMULT_ID\s*=", re.IGNORECASE)


def inspect_fds(path: str) -> dict[str, Any]:
    with open(path, "rb") as fb:
        raw = fb.read()
    raw_non_ascii = any(b > 127 for b in raw)
    text = raw.decode("utf-8", errors="replace")
    # drop comment lines outside namelists is not needed: FDS ignores text
    # outside '&...' groups, and the regex only matches '&MESH ... /'
    meshes = []
    uses_mult = False
    for m in _MESH_RE.finditer(text):
        body = m.group(1)
        ijk = _IJK_RE.search(body)
        cells = int(ijk.group(1)) * int(ijk.group(2)) * int(ijk.group(3)) if ijk else None
        mpi = _MPI_RE.search(body)
        meshes.append({"cells": cells, "mpi_process": int(mpi.group(1)) if mpi else None})
        uses_mult |= bool(_MULT_RE.search(body))

    groups: dict[Any, int] = {}
    for i, m in enumerate(meshes):
        key = m["mpi_process"] if m["mpi_process"] is not None else f"mesh{i}"
        groups[key] = groups.get(key, 0) + (m["cells"] or 0)

    cells = [m["cells"] for m in meshes if m["cells"]]
    return {
        "file": os.path.basename(path),
        "meshes": len(meshes),
        "total_cells": sum(cells),
        "min_cells_per_mesh": min(cells) if cells else None,
        "max_cells_per_mesh": max(cells) if cells else None,
        "uses_mpi_process": any(m["mpi_process"] is not None for m in meshes),
        "mpi_groups": len(groups),
        "cells_per_group": sorted(groups.values()),
        "uses_mult_id": uses_mult,
        "mpi_process_values": [m["mpi_process"] for m in meshes],
        "slow_devc_on_amd": sorted({q.upper() for q in re.findall(
            r"QUANTITY\s*=\s*'(VISIBILITY|RADIATIVE HEAT FLUX|GAUGE HEAT FLUX GAS)'", text, re.IGNORECASE)}),
        "mesh_boundary_vents": bool(re.search(r"&VENT\b[^/]*?\bMB\s*=", text, re.IGNORECASE | re.DOTALL)),
        "non_ascii": raw_non_ascii,
        "has_part": bool(re.search(r"^\s*&PART\b", text, re.IGNORECASE | re.MULTILINE)),
        "t_end": _t_end(text),
        "max_ramp_t": _max_ramp_t(text),
        "dt_restart": _dt_restart(text),
        "evacuation": bool(re.search(r"&MESH\b[^/]*?\bEVACUATION\s*=\s*\.?T", text, re.IGNORECASE | re.DOTALL)),
    }


def _num(s: str) -> float | None:
    try:
        return float(s.replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def _t_end(text: str) -> float | None:
    m = re.search(r"\bT_END\s*=\s*([-+0-9.eEdD]+)", text)
    return _num(m.group(1)) if m else None


def _dt_restart(text: str) -> float | None:
    m = re.search(r"&DUMP\b[^/]*?\bDT_RESTART\s*=\s*([-+0-9.eEdD]+)", text, re.IGNORECASE | re.DOTALL)
    return _num(m.group(1)) if m else None


def _max_ramp_t(text: str) -> float | None:
    ts = [_num(t) for t in re.findall(r"&RAMP\b[^/]*?\bT\s*=\s*([-+0-9.eEdD]+)", text,
                                       re.IGNORECASE | re.DOTALL)]
    ts = [t for t in ts if t is not None]
    return max(ts) if ts else None


def fds_auto_split(fds: dict | None) -> bool:
    """True if cloudHPC will split the single &MESH across the physical cores:
    one &MESH, no MULT_ID / MPI_PROCESS, no mesh-boundary (MB) vents, and
    enough cells for at least two pieces of ~15,000 cells."""
    if not fds:
        return False
    return (fds.get("meshes") == 1 and not fds.get("uses_mult_id")
            and not fds.get("uses_mpi_process") and not fds.get("mesh_boundary_vents")
            and (fds.get("total_cells") or 0) >= 30_000)


def _openfoam_cells(folder: str) -> int | None:
    """Read nCells from the polyMesh/owner header, if the mesh exists."""
    for owner in (os.path.join(folder, "constant", "polyMesh", "owner"),
                  os.path.join(folder, "constant", "polyMesh", "owner.gz")):
        if not os.path.exists(owner):
            continue
        try:
            if owner.endswith(".gz"):
                import gzip
                with gzip.open(owner, "rt", errors="replace") as f:
                    head = f.read(4096)
            else:
                with open(owner, "r", errors="replace") as f:
                    head = f.read(4096)
        except OSError:
            return None
        m = re.search(r"nCells\s*:?\s*(\d+)", head)
        if m:
            return int(m.group(1))
    return None


def _count_inp_nodes(path: str) -> int | None:
    """Count node lines in *NODE blocks of a CalculiX .inp (top file only)."""
    nodes, in_node = 0, False
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("**"):          # comment
                    continue
                if s.startswith("*"):           # keyword line
                    in_node = s.split(",")[0].strip().upper() == "*NODE"
                    continue
                if in_node and s:
                    nodes += 1
    except OSError:
        return None
    return nodes or None


def _saturne_case(folder: str) -> str | None:
    """code_saturne case = folder with DATA and SRC, at the root or one level down."""
    if os.path.isdir(os.path.join(folder, "DATA")) and os.path.isdir(os.path.join(folder, "SRC")):
        return "."
    for d in sorted(os.listdir(folder)):
        p = os.path.join(folder, d)
        if os.path.isdir(os.path.join(p, "DATA")) and os.path.isdir(os.path.join(p, "SRC")):
            return d
    return None


def inspect_case(folder: str) -> dict[str, Any]:
    """Detect the solver of a local case folder and extract size information."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Folder not found: {folder}")

    total_bytes, n_files = 0, 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.startswith("."):
                continue
            n_files += 1
            try:
                total_bytes += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass

    info: dict[str, Any] = {
        "folder": folder,
        "storage_name": os.path.basename(folder.rstrip(os.sep)),
        "files": n_files,
        "size_mb": round(total_bytes / 1e6, 1),
        "family": "other",
        "notes": [],
    }

    def files(pattern: str) -> list[str]:
        return sorted(os.path.basename(x) for x in glob.glob(os.path.join(folder, pattern))
                      if os.path.isfile(x))

    has_foam_case = os.path.exists(os.path.join(folder, "system", "controlDict"))
    if files("runScript.py") or (files("preProcessing.sh") and has_foam_case):
        info["family"] = "dafoam"
        info["dafoam_entry"] = [f for f in ("preProcessing.sh", "runScript.py", "Allrun")
                                if os.path.exists(os.path.join(folder, f))]
        dpd = os.path.join(folder, "system", "decomposeParDict")
        if os.path.exists(dpd):
            with open(dpd, "r", errors="replace") as f:
                m = re.search(r"^\s*numberOfSubdomains\s+(\d+)\s*;", f.read(), re.MULTILINE)
            info["number_of_subdomains"] = int(m.group(1)) if m else None
        info["cells"] = _openfoam_cells(folder)
        return info

    fds = sorted(glob.glob(os.path.join(folder, "*.fds")) + glob.glob(os.path.join(folder, "*.FDS")))
    if fds:
        info["family"] = "fds"
        info["fds"] = inspect_fds(fds[0])
        if len(fds) > 1:
            info["notes"].append(f"{len(fds)} .fds files found; analysed {os.path.basename(fds[0])}. "
                                 "Only the first one (alphabetical order) is run: keep one "
                                 ".fds file per case folder.")
        return info

    if os.path.exists(os.path.join(folder, "system", "controlDict")):
        info["family"] = "openfoam"
        info["cells"] = _openfoam_cells(folder)
        if info["cells"] is None:
            info["notes"].append("No constant/polyMesh found (mesh generated at run time?): "
                                 "ask the user for the expected number of cells.")
        if not os.path.exists(os.path.join(folder, "system", "decomposeParDict")):
            info["notes"].append("system/decomposeParDict is missing: cloudHPC adds a default "
                                 "one (scotch). Template: https://github.com/CFD-FEA-SERVICE/CloudHPC/blob/master/template/OpenFOAM/system/decomposeParDict")
        procs = glob.glob(os.path.join(folder, "processor*"))
        if procs:
            info["notes"].append(f"{len(procs)} processor* folders found: they are uploaded too "
                                 "and may be large; remove them unless you are restarting a run.")
        return info

    exports = glob.glob(os.path.join(folder, "*.export"))
    if exports or glob.glob(os.path.join(folder, "*.comm")):
        info["family"] = "code_aster"
        if exports:
            with open(exports[0], "r", errors="replace") as f:
                export_text = f.read()
            m = re.search(r"^P\s+mpi_nbcpu\s+(\d+)", export_text, re.MULTILINE)
            info["mpi_nbcpu"] = int(m.group(1)) if m else None
            # input files referenced by the .export (F <type> <path> D ...): paths are
            # reduced to the file name, so the files must be in the case folder
            refs = re.findall(r"^F\s+(\w+)\s+(\S+)\s+D\b", export_text, re.MULTILINE)
            info["export_inputs"] = sorted({os.path.basename(p.replace("\\", "/")) for _, p in refs})
        comm_text = ""
        for c in glob.glob(os.path.join(folder, "*.comm")):
            try:
                with open(c, "r", errors="replace") as f:
                    comm_text += f.read()
            except OSError:
                pass
        info["comm_parallel"] = bool(re.search(r"NB_SOUS_DOMAINE|NIVEAU_PARALLELISME", comm_text))
        info["notes"].append("Node count not read automatically for code_aster (MED mesh): "
                             "ask the user for the number of nodes.")
        return info

    inps = glob.glob(os.path.join(folder, "*.inp"))
    if inps:
        info["family"] = "calculix"
        info["input_files"] = sorted(os.path.basename(x) for x in inps)
        info["nodes"] = _count_inp_nodes(sorted(inps)[0])
        if info["nodes"] is None:
            info["notes"].append("Nodes not found in the main .inp (meshes in *INCLUDE files?): "
                                 "ask the user for the number of nodes.")
        return info

    for fam, pattern in (("telemac", "*.cas"), ("energyplus", "*.idf"), ("contam", "*.prj")):
        found = files(pattern)
        if found:
            info["family"] = fam
            info["input_files"] = found
            if fam == "energyplus":
                try:
                    with open(os.path.join(folder, found[0]), "r", errors="replace") as f:
                        text = f.read()
                    m = re.search(r"^\s*Version\s*,\s*([0-9][0-9.]*)\s*;", text,
                                  re.IGNORECASE | re.MULTILINE)
                    info["idf_version"] = m.group(1) if m else None
                    info["hvac_templates"] = bool(re.search(r"^\s*HVACTemplate:", text,
                                                            re.IGNORECASE | re.MULTILINE))
                except OSError:
                    pass
            return info

    if files("Makefile") and files("*.cpp"):
        info["family"] = "openlb"
        info["executables"] = [f for f in os.listdir(folder)
                               if os.path.isfile(os.path.join(folder, f))
                               and os.access(os.path.join(folder, f), os.X_OK)]
        return info

    ins = files("in.*")
    if ins:
        info["family"] = "liggghts"
        info["input_files"] = files("in*")
        return info

    if (glob.glob(os.path.join(folder, "*.rad")) or glob.glob(os.path.join(folder, "*.key"))):
        info["family"] = "openradioss"
        info["input_files"] = sorted(os.path.basename(x) for x in
                                     glob.glob(os.path.join(folder, "*.rad"))
                                     + glob.glob(os.path.join(folder, "*.key")))
        info["notes"].append("Ask the user for the number of elements.")
        return info

    saturne = _saturne_case(folder)
    if saturne is not None:
        info["family"] = "codesaturne"
        info["saturne_case"] = saturne
        info["notes"].append("Ask the user for the number of cells.")
        return info

    if files("*.swn"):
        info["family"] = "swan"
        info["input_files"] = files("*.swn")
        return info

    if files("params.txt"):
        info["family"] = "xbeach"
        return info

    cfgs = [c for c in files("*.cfg") if c != "config_CFD.cfg"]
    if cfgs:
        info["family"] = "su2"
        info["input_files"] = cfgs
        info["notes"].append("Ask the user for the number of cells.")
        return info

    if files("*.sh") or files("*.py"):
        info["family"] = "custom"
        info["scripts"] = files("*.sh") + files("*.py")
        info["has_requirements"] = bool(files("requirements.txt"))
        return info

    info["notes"].append("Solver not recognised from the files: ask the user which solver to use.")
    return info


# ----------------------------------------------------------------- advisor

MEMORY_ERROR_PATTERNS = (
    "unable to allocate", "out of memory", "cannot allocate memory",
    "oom-kill", "oom killer", "std::bad_alloc", "insufficient memory",
    "memory allocation failed", "killed signal 9",
)


def next_ram(ram: str) -> str | None:
    """Next RAM tier after a memory failure (highcpu -> standard -> highmem)."""
    if ram in HT_RAM_LADDER:
        i = HT_RAM_LADDER.index(ram)
        return HT_RAM_LADDER[i + 1] if i + 1 < len(HT_RAM_LADDER) else None
    return None


def looks_like_memory_error(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in MEMORY_ERROR_PATTERNS)


def _no_single_highcpu(result: dict) -> dict:
    """1 vCPU on highcpu has too little RAM for any engineering solver."""
    if result.get("cpu") == 1 and result.get("ram") == "highcpu":
        result["ram"] = "standard"
        result.setdefault("notes", []).append(
            "1 vCPU highcpu does not have enough RAM for engineering solvers "
            "(the run fails at start-up): using standard.")
    return result


def _pick_cpu_up(needed: int, options: list[int]) -> int:
    """Smallest available vCPU option >= needed (or the largest option)."""
    opts = sorted(options) or [1]
    fitting = [o for o in opts if o >= max(needed, 1)]
    return fitting[0] if fitting else opts[-1]


def _pick_cpu(needed: int, options: list[int]) -> int:
    """Largest available vCPU option <= needed (at least the smallest option)."""
    opts = sorted(options) or [1]
    fitting = [o for o in opts if o <= max(needed, 1)]
    return fitting[-1] if fitting else opts[0]


def _suggest(family: str,
            cpu_options: list[int],
            ram_options: list[str],
            cells: int | None = None,
            nodes: int | None = None,
            elements: int | None = None,
            fds: dict | None = None,
            prefer_speed: bool = False) -> dict[str, Any]:
    """Return a vCPU/RAM recommendation with reasoning and warnings."""
    notes: list[str] = []
    warnings: list[str] = []
    ram_avail = set(ram_options) if ram_options else set(HT_RAM_LADDER + PHYSICAL_RAM)

    def physical_ram() -> str:
        choice = "hypercore" if prefer_speed else "highcore"
        return choice if choice in ram_avail else next(
            (r for r in PHYSICAL_RAM if r in ram_avail), "highcore")

    if family == "fds":
        ram = physical_ram() if prefer_speed else "highcpu"
        per_group = 1 if ram in PHYSICAL_RAM else 2
        if fds:
            groups = fds["mpi_groups"] or 1
            if fds_auto_split(fds):
                limit = max(fds["total_cells"] // 15_000, 1)
                needed = limit * per_group
                notes.append(f"Single &MESH with {fds['total_cells']:,} cells: cloudHPC splits it "
                             f"automatically into one piece per physical core (up to ~{limit} "
                             "pieces: at least 15,000 cells and 12 cells per direction each; the "
                             "split must divide the IJK cell counts exactly, so IJK values with "
                             "many divisors split best). Check the result in Smokeview.")
                if needed < 2 * per_group:
                    warnings.append("Fewer than 2 physical cores: the mesh will not be split.")
            else:
                needed = groups * per_group
            small = [c for c in fds["cells_per_group"] if c and c < 15_000]
            if small and fds["meshes"] > 1:
                warnings.append(f"{len(small)} mesh/process groups have < 15,000 cells: use "
                                "MPI_PROCESS to group small meshes on one core.")
            cpg = [c for c in fds["cells_per_group"] if c]
            if cpg and min(cpg) > 0 and max(cpg) / min(cpg) > 2:
                warnings.append(f"Unbalanced load: largest group {max(cpg):,} cells vs smallest "
                                f"{min(cpg):,}. Rebalance with MPI_PROCESS or split large meshes.")
            if fds["meshes"] == 1 and fds.get("mesh_boundary_vents"):
                notes.append("The single &MESH is not split automatically because the case has "
                             "&VENT with MB= (mesh boundary vents).")
            if fds.get("uses_mult_id"):
                warnings.append("MULT_ID is used: the real number of meshes is higher than the "
                                "&MESH lines counted; recompute vCPU with the multiplied meshes.")
            notes.append(f"Rule: vCPU = {'MPI_PROCESS groups' if fds['uses_mpi_process'] else '&MESH'}"
                         f" x {per_group} on {ram}.")
        else:
            return {"error": "For FDS, pass the parsed .fds info (use inspect_case) "
                             "or the number of &MESH / MPI_PROCESS groups."}
        decomposed = fds_auto_split(fds)
        if decomposed:
            # cloudHPC splits the single mesh to fit the vCPU: rounding down is safe
            cpu = _pick_cpu(needed, cpu_options)
        else:
            # one MPI process per mesh/group: round UP (fewer vCPU than groups stops
            # the run with "low vCPU selected"; fewer physical cores than groups is slow)
            cpu = _pick_cpu_up(needed, cpu_options)
            if cpu < needed:
                warnings.append(f"{needed} vCPU needed but the largest option is {cpu}: group "
                                "meshes with MPI_PROCESS to reduce the cores required.")
        if cpu != needed:
            notes.append(f"{needed} vCPU would be ideal; chosen option is {cpu}.")
        if ram == "highcpu":
            notes.append("RAM: start on highcpu (cheapest). If the run fails with a memory "
                         "error, relaunch on standard, then highmem. More RAM does not speed "
                         "up the run.")
            if fds.get("slow_devc_on_amd"):
                notes.append(f"The case has DEVC with {', '.join(fds['slow_devc_on_amd'])}: they "
                             "slow down AMD CPUs, so if delivery time matters prefer "
                             "hypercpu/hypercore (Intel).")
        return {"family": family, "cpu": cpu, "ram": ram, "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "openfoam":
        if not cells:
            return {"error": "Number of cells needed (read from constant/polyMesh or ask the user)."}
        needed = max(cells // 50_000, 2)
        cpu = max(_pick_cpu(needed, cpu_options), _pick_cpu_up(2, cpu_options))
        notes.append("cloudHPC always runs OpenFOAM in parallel: at least 2 vCPU on "
                     "highcore/hypercore.")
        notes.append(f"Rule: at least 50,000 cells per core -> max {needed} cores "
                     f"for {cells:,} cells; nearest option {cpu}.")
        notes.append("OpenFOAM uses MPI only: use highcore or hypercore (hypercore is the "
                     "faster CPU generation). cloudHPC updates decomposeParDict automatically.")
        return {"family": family, "cpu": cpu, "ram": physical_ram(), "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "calculix":
        if nodes:
            needed = max(nodes // 200_000, 1)
            cpu = _pick_cpu(needed, cpu_options)
            notes.append(f"Rule (blog benchmark): PARDISO scales up to ~200,000 nodes per core "
                         f"-> about {needed} cores for {nodes:,} nodes.")
        else:
            cpu, needed = _pick_cpu(4, cpu_options), 4
            warnings.append("Number of nodes unknown: 4 vCPU is a conservative starting point.")
        notes.append("Prefer a PARDISO version (calculiX-2.19-PARDISO multi-threaded, or "
                     "calculiX-2.18-PARDISO-MPI) and set SOLVER=PARDISO on *STATIC; SPOOLES "
                     "scales poorly.")
        notes.append("RAM: start on highcpu, then standard, then highmem if it fails for memory.")
        return {"family": family, "cpu": cpu, "ram": "highcpu", "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "code_aster":
        if nodes:
            ranks = max(nodes // 100_000, 1)
            needed = ranks * 2
            cpu = _pick_cpu(needed, cpu_options)
            notes.append(f"Rule (blog benchmark): >= 100,000 nodes per MPI process -> {ranks} "
                         f"processes, 2 vCPU each = {needed} vCPU. Set 'P mpi_nbcpu "
                         f"{max(cpu // 2, 1)}' in the .export file.")
        else:
            cpu, needed = _pick_cpu(8, cpu_options), 8
            warnings.append("Number of nodes unknown: 8 vCPU (4 ranks x 2 threads) as a start.")
        notes.append("Use an _mpi version (e.g. codeAster-17.0_mpi) with PETSc or MUMPS and "
                     "MATR_DISTRIBUEE='OUI'. mpi_nbcpu is used only if the .comm contains "
                     "NB_SOUS_DOMAINE or NIVEAU_PARALLELISME; cloudHPC sets the OpenMP threads "
                     "per process to vCPU / mpi_nbcpu / 2, so select at least 2 x mpi_nbcpu vCPU.")
        notes.append("RAM: code_aster is memory hungry. Start on highcpu, then standard, then "
                     "highmem if it fails for memory.")
        return {"family": family, "cpu": cpu, "ram": "highcpu", "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "openradioss":
        if not elements:
            return {"error": "Number of elements needed (ask the user)."}
        needed = max(elements // 25_000, 1)
        cpu = _pick_cpu(needed, cpu_options)
        notes.append(f"Rule (blog benchmark): 20,000-30,000 elements per core -> about "
                     f"{needed} cores for {elements:,} elements; physical cores are much "
                     "faster than hyperthreads (hypercore was the most cost-efficient).")
        return {"family": family, "cpu": cpu, "ram": "hypercore" if "hypercore" in ram_avail else physical_ram(),
                "ideal_cpu": needed, "notes": notes, "warnings": warnings}

    if family == "su2":
        warnings.append("No validated cells-per-core rule for SU2 yet: suggest a moderate "
                        "core count and let the user decide.")
        cpu = _pick_cpu(max((cells or 0) // 50_000, 1) if cells else 16, cpu_options)
        return {"family": family, "cpu": cpu, "ram": physical_ram(),
                "notes": ["SU2 runs with MPI: use highcore or hypercore."], "warnings": warnings}

    if family == "dafoam":
        needed = max((cells or 0) // 50_000, 2) if cells else 8
        cpu = max(_pick_cpu(needed, cpu_options), _pick_cpu_up(2, cpu_options))
        notes.append("DAFoam runs one MPI process per physical core: use highcore/hypercore and "
                     f"set numberOfSubdomains {cpu} in system/decomposeParDict (cloudHPC does "
                     "not adjust it for DAFoam).")
        if not cells:
            warnings.append("Number of cells unknown: 8 vCPU as a starting point.")
        return {"family": family, "cpu": cpu, "ram": physical_ram(), "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "contam":
        return {"family": family, "cpu": _pick_cpu_up(2, cpu_options), "ram": "highcpu",
                "notes": ["CONTAM runs on a single core: extra vCPU are not used."],
                "warnings": warnings}

    if family == "energyplus":
        return {"family": family, "cpu": _pick_cpu(4, cpu_options), "ram": "highcpu",
                "notes": ["EnergyPlus 9.6.0 and later use all vCPU as threads; 2-4 vCPU are "
                          "enough for most buildings (9.4.0 uses one thread)."],
                "warnings": warnings}

    if family in ("liggghts", "telemac"):
        name = "LIGGGHTS" if family == "liggghts" else "openTELEMAC"
        return {"family": family, "cpu": _pick_cpu(8, cpu_options), "ram": physical_ram(),
                "notes": [f"{name} runs one MPI process per physical core: use highcore or "
                          "hypercore."],
                "warnings": ["No validated scaling rule yet: 8 vCPU as a starting point; ask "
                             "the user about the model size."]}

    if family == "codesaturne":
        needed = max((cells or 0) // 50_000, 2) if cells else 8
        cpu = _pick_cpu(needed, cpu_options)
        notes.append("code_saturne runs one MPI process per physical core (1 thread each): use "
                     "highcore or hypercore.")
        if cells:
            notes.append(f"Starting rule: ~50,000 cells per core -> {needed} cores for {cells:,} cells.")
        else:
            warnings.append("Number of cells unknown: 8 vCPU as a starting point.")
        return {"family": family, "cpu": cpu, "ram": physical_ram(), "ideal_cpu": needed,
                "notes": notes, "warnings": warnings}

    if family == "swan":
        return {"family": family, "cpu": _pick_cpu(8, cpu_options), "ram": "highcpu",
                "notes": ["SWAN runs multi-threaded (OpenMP) on all vCPU: hyperthreaded types "
                          "are fine."],
                "warnings": ["No validated scaling rule yet: 8 vCPU as a starting point."]}

    if family == "xbeach":
        return {"family": family, "cpu": _pick_cpu(8, cpu_options), "ram": physical_ram(),
                "notes": ["XBeach (MPI) runs one process per physical core: use highcore or "
                          "hypercore."],
                "warnings": ["No validated scaling rule yet: 8 vCPU as a starting point."]}

    if family == "custom":
        return {"family": family, "cpu": _pick_cpu(2, cpu_options), "ram": "standard",
                "notes": ["Custom scripts use the cores only if they are written to run in "
                          "parallel: size the machine on what the scripts do."],
                "warnings": warnings}

    if family == "openlb":
        return {"family": family, "cpu": _pick_cpu(4, cpu_options), "ram": "highcpu",
                "notes": ["The OpenLB executable runs as a single process (no MPI launcher); "
                          "more vCPU help only if OpenLB is built with OpenMP."],
                "warnings": warnings}

    return {"family": family, "cpu": _pick_cpu(4, cpu_options), "ram": "highcpu",
            "notes": ["No specific rule: 4 vCPU on highcpu as a starting point."],
            "warnings": ["Confirm the solver and settings with the user."]}


def suggest(family: str, cpu_options: list[int], ram_options: list[str], **kw) -> dict[str, Any]:
    result = _suggest(family, cpu_options, ram_options, **kw)
    if "error" in result or family == "other":
        return result
    return _no_single_highcpu(result)

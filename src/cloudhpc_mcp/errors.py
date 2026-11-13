"""Known cloudHPC / solver messages and how to fix them.

Source: https://docs.cloudhpc.cloud/errors/  A run can end as COMPLETED even
when the solver failed, so the simulation output must always be checked.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

DOCS = "https://docs.cloudhpc.cloud/errors/"

# id, severity, regex (case-insensitive), cause, fix, docs anchor
CATALOG: list[dict[str, str]] = [
    {"id": "precice_early_exit", "severity": "error",
     "pattern": r"exited before the coupling was complete",
     "cause": "A preCICE participant ended before the coupling end time (e.g. OpenFOAM SIMPLE "
              "stopped by residualControl, endTime or FDS T_END lower than max-time): the "
              "others were stopped because they would have waited forever.",
     "fix": "Remove residualControl from SIMPLE and set endTime / T_END at least equal to "
            "max-time of precice-config.xml.", "anchor": ""},
    {"id": "precice_waiting", "severity": "error",
     "pattern": r"waiting for coupling data that will never arrive",
     "cause": "One participant's solver finished while another was still in its solver after "
              "10 minutes: it was stopped.",
     "fix": "Make all participants end at the coupling end time (max-time): check endTime, "
            "T_END and the end conditions of each solver.", "anchor": ""},
    {"id": "precice_hung", "severity": "error",
     "pattern": r"coupled run looks hung|reported a fatal error and is still running",
     "cause": "The coupled run was stopped because a participant printed a fatal error without "
              "exiting, or no participant wrote to its log for an hour.",
     "fix": "Read the log.<participant> named in the message for the first error.", "anchor": ""},
    {"id": "precice_convergence", "severity": "warning",
     "pattern": r"coupling not converging",
     "cause": "Many implicit coupling time windows reached max-iterations without converging: "
              "results are likely inaccurate.",
     "fix": "Use relTol 0 on the *Final solvers of the fluid and check the convergence measures "
            "and acceleration in precice-config.xml.", "anchor": ""},
    {"id": "precice_no_folder", "severity": "error",
     "pattern": r"has no supported solver folder",
     "cause": "A participant of precice-config.xml has no folder that cloudHPC can run.",
     "fix": "Add one folder per participant (OpenFOAM, CalculiX, code_aster or FDS), named as in "
            "solver_guide('precice').", "anchor": ""},
    {"id": "xbeach_error", "severity": "error",
     "pattern": r"XBeach stopped with an error|XBeach reported an error|did not reach 'End of program xbeach'",
     "cause": "XBeach stopped with an error or ended before the end of the simulation.",
     "fix": "Read XBerror.txt (printed in the output) and XBlog.txt: they name the params.txt "
            "keyword or input file that failed.", "anchor": ""},
    {"id": "xbeach_nonh", "severity": "info",
     "pattern": r"XBeach runs on 1 MPI process",
     "cause": "Non-hydrostatic case (wavemodel = nonh): XBeach ran on one process.",
     "fix": "Select 2 vCPU for these cases: more cores stay idle.", "anchor": ""},
    {"id": "pfp_gpu", "severity": "error",
     "pattern": r"No NVIDIA GPU found",
     "cause": "PhasicFlowPlus runs the DEM on the GPU and was launched on a CPU instance.",
     "fix": "Relaunch on a GPU RAM type (e.g. basegpu).", "anchor": ""},
    {"id": "pfp_application", "severity": "error",
     "pattern": r"system/controlDict 'application' is",
     "cause": "The CFD-DEM solver in system/controlDict is not a PhasicFlowPlus solver.",
     "fix": "Set application to unresolvedSpherePFPlus, unresolvedGrainPFPlus, "
            "resolvedSpherePFPlus or resolvedSphereVoFPFPlus.", "anchor": ""},
    {"id": "pfp_inputs", "severity": "error",
     "pattern": r"No PhasicFlowPlus case found|Time folder for CFD-DEM startTime|"
                r"No fluid velocity field U|No mesh: provide constant/polyMesh",
     "cause": "The PhasicFlowPlus case is incomplete (case files, mesh, DEM stage end time or "
              "initial fluid fields).",
     "fix": "Follow the message; see solver_guide('phasicflow') for the expected layout.",
     "anchor": ""},
    {"id": "telemac_steering", "severity": "error",
     "pattern": r"Could not find your steering file",
     "cause": "TELEMAC cannot find the steering (.cas) file it was asked to run.",
     "fix": "Check the .cas name and the files it references (names and paths must match "
            "the files in the case folder, case included).", "anchor": ""},
    {"id": "telemac_exception", "severity": "error",
     "pattern": r"TelemacException",
     "matched": r"TelemacException:\s*\S",
     "cause": "TELEMAC stopped with an error.",
     "fix": "Read the TelemacException message and the lines just above the traceback in "
            "openTELEMAC.log: they name the file, keyword or program that failed.",
     "anchor": ""},
    {"id": "kratos_app", "severity": "error",
     "pattern": r"needs the Kratos application '",
     "cause": "The case uses a Kratos application that is not installed on cloudHPC.",
     "fix": "Use one of the installed applications listed in the message, or ask cloudHPC "
            "support to add it.", "anchor": ""},
    {"id": "kratos_stage", "severity": "error",
     "pattern": r"analysis type cannot be determined",
     "cause": "Kratos cannot tell which analysis to run (MainKratos.py is not executed).",
     "fix": "Add \"analysis_stage\" to ProjectParameters.json, e.g. "
            "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis.",
     "anchor": ""},
    {"id": "kratos_inputs", "severity": "error",
     "pattern": r"input files referenced by the parameters file are missing",
     "cause": "The .mdpa mesh or materials file named in the parameters is not in the case folder.",
     "fix": "Upload the missing files or fix input_filename / materials_filename.", "anchor": ""},
    {"id": "kratos_mpi", "severity": "info",
     "pattern": r"no TrilinosApplication: running in OpenMP",
     "cause": "parallel_type MPI was run in OpenMP (Trilinos is not available).",
     "fix": "Nothing to do; if the linear solver fails, use amgcl or skyline_lu_factorization.",
     "anchor": ""},
    # ---------------------------------------------------------------- RAM
    {"id": "ram_high", "severity": "warning",
     "pattern": r"RAM used > \d+(\.\d+)?%",
     "cause": "RAM usage above 80%: the run may soon fail for lack of memory.",
     "fix": "Increase vCPU or move to the next RAM tier (highcpu -> standard -> highmem).",
     "anchor": "low_ram_available"},
    {"id": "ram_out", "severity": "error",
     "pattern": r"SWAP unresolved after \d+ attempts|KILLED BY SIGNAL: 9|Unable to allocate .* bytes|"
                r"out of memory|Cannot allocate memory|std::bad_alloc|oom[- ]kill",
     "cause": "The run ran out of RAM.",
     "fix": "Relaunch with more RAM: next tier (highcpu -> standard -> highmem) or more vCPU. "
            "1 vCPU has too little RAM for engineering solvers.",
     "anchor": "low_ram_available"},
    # --------------------------------------------------------------- disk
    {"id": "disk_80", "severity": "warning",
     "pattern": r"HARD DISK used > 80",
     "cause": "Disk above 80%. At 90% cloudHPC soft-stops the run automatically.",
     "fix": "Write fewer/lighter outputs (e.g. lower output frequency) or soft stop the run to "
            "keep the data produced so far.",
     "anchor": "hard_disk_use"},
    {"id": "disk_90", "severity": "error",
     "pattern": r"HARD DISK used > 90|AUTOMATIC SOFT STOP",
     "cause": "Disk above 90%: cloudHPC soft-stopped the run.",
     "fix": "Reduce the amount of output written by the solver and relaunch.",
     "anchor": "hard_disk_use"},
    # ------------------------------------------------------------- upload
    {"id": "archive_folder", "severity": "error",
     "pattern": r"Not found folder .* inside of compressed file",
     "cause": "The archive layout does not match the upload method.",
     "fix": "Either archive a folder named exactly like the archive and upload it to the storage "
            "root, or archive the files directly (no wrapping folder) and upload into a folder. "
            "upload_folder uses the second method automatically.",
     "anchor": "incorrect_compressed_file"},
    {"id": "folder_name", "severity": "error",
     "pattern": r"FOLDER .* not detected|not recognized as an available compressed format",
     "cause": "Folder/file name not accepted or archive format not supported.",
     "fix": "Remove special characters , ( ) ' $ ~ \" # from folder/file names; use zip, "
            "tar.gz, 7z, rar or xz.",
     "anchor": "incorrect_file_or_folder_name"},
    # ---------------------------------------------------------------- FDS
    {"id": "fds_missing", "severity": "error",
     "pattern": r"No FDS file detected",
     "cause": "No .fds file in the case folder.",
     "fix": "Upload the .fds input (exported from your pre-processor) in the case folder.",
     "anchor": "fds_incorrect_settings"},
    {"id": "fds_psm", "severity": "error",
     "pattern": r"is a pyrosim file",
     "cause": "A PyroSim .psm file was uploaded instead of the .fds input.",
     "fix": "Export the .fds file from PyroSim and upload that.",
     "anchor": "pyrosim_input_file"},
    {"id": "fds_mpi_order", "severity": "error",
     "pattern": r"MPI_PROCESS parameter must be in ASCENDING ORDER|MPI_MPI_PROCESS incorrect",
     "cause": "&MESH lines are not ordered by MPI_PROCESS.",
     "fix": "Reorder the &MESH lines so MPI_PROCESS values are ascending.",
     "anchor": "scalability_issue_with_mpi_process"},
    {"id": "fds_low_vcpu", "severity": "error",
     "pattern": r"low vCPU selected|Number of MESHES higher than available CORES",
     "cause": "More meshes/MPI groups than vCPU.",
     "fix": "Select at least one vCPU per mesh/MPI group (two per group on highcpu/standard/"
            "highmem/hypercpu for best speed) or group meshes with MPI_PROCESS.",
     "anchor": "scalability_issue_with_mpi_process"},
    {"id": "fds_warnings", "severity": "warning",
     "pattern": r"no other WARNING messages showed",
     "cause": "FDS printed many warnings (often objects or devices outside every mesh).",
     "fix": "Check the solver log (<CHID>.out / .log) and fix the geometry/devices.",
     "anchor": "warning_messages_by_fds"},
    {"id": "fds_threads", "severity": "warning",
     "pattern": r"high number of threads used",
     "cause": "Too few meshes for the vCPU selected: poor scalability.",
     "fix": "Split the mesh into more &MESH or select fewer vCPU.",
     "anchor": "high_number_of_threads"},
    {"id": "fds_pressure_zones", "severity": "warning",
     "pattern": r"high number of Pressure Zones",
     "cause": "Many pressure zones: the run may scale poorly.",
     "fix": "Add MINIMUM_ZONE_VOLUME=1.0 to &MISC (NO_PRESSURE_ZONES=T only for debugging).",
     "anchor": "high_number_of_pressure_zones"},
    {"id": "fds_non_ascii", "severity": "warning",
     "pattern": r"FDS file with non ASCII characters",
     "cause": "The .fds file contains non-ASCII characters (accents, symbols, special quotes).",
     "fix": "Remove them (also from comments and IDs) and save the file as plain text/UTF-8 "
            "without special characters: FDS may misread the input.",
     "anchor": "fds_incorrect_settings"},
    {"id": "fds_mult_mesh", "severity": "warning",
     "pattern": r"MULT applied to MESH",
     "cause": "&MESH uses MULT_ID: cloudHPC cannot count the multiplied meshes correctly.",
     "fix": "Write the meshes explicitly (or check vCPU against the real number of meshes).",
     "anchor": "fds_incorrect_settings"},
    {"id": "fds_part", "severity": "warning",
     "pattern": r"PART detected - FDS scalability may be poor",
     "cause": "Lagrangian particles (&PART) reduce FDS parallel scalability.",
     "fix": "Expect lower speed-up with many vCPU; balance meshes carefully.",
     "anchor": ""},
    {"id": "fds_ramp_tend", "severity": "warning",
     "pattern": r"max\(RAMP_T\) .*> T_END",
     "cause": "A &RAMP is defined beyond T_END: the end time may be shorter than intended.",
     "fix": "Check T_END on &TIME against the ramps (HRR curves, activations).",
     "anchor": ""},
    {"id": "fds_no_restart_files", "severity": "info",
     "pattern": r"NO RESTART FILE => NOTHING TO DO",
     "cause": "Normal check at start: no .restart files, so the run starts from the beginning.",
     "fix": "Nothing to do. It is not an error.",
     "anchor": ""},
    {"id": "fds_restarted", "severity": "info",
     "pattern": r"YES RESTART FILE .*=> ADD RESTART|\*\* RESTART TRUE \*\*",
     "cause": "The run resumed from .restart files (e.g. after a preemptible-instance restart "
              "or a relaunch).",
     "fix": "Nothing to do: the restart is automatic. Time steps after the last restart dump "
            "are recomputed.",
     "anchor": ""},
    {"id": "fds_evac_restart", "severity": "info",
     "pattern": r"RESTART NOT POSSIBLE FOR FDS\+EVAC",
     "cause": "FDS+EVAC cases cannot be restarted.",
     "fix": "Relaunch the case from the beginning.",
     "anchor": ""},
    {"id": "fds_devc_amd", "severity": "warning",
     "pattern": r"DEVC for .* may slow down your simulation",
     "cause": "VISIBILITY / RADIATIVE HEAT FLUX / GAUGE HEAT FLUX GAS devices slow down AMD CPUs.",
     "fix": "Run on hypercpu or hypercore (Intel) instances.",
     "anchor": "devc_affecting_performances"},
    # ----------------------------------------------------------- OpenFOAM
    {"id": "of_controldict", "severity": "error",
     "pattern": r"Cannot find system/controlDict",
     "cause": "system/controlDict not found: case uploaded with the wrong layout.",
     "fix": "The case folder must contain 0, constant and system at its root.",
     "anchor": "incorrect_dictionary"},
    {"id": "of_nproc", "severity": "error",
     "pattern": r"(openFoam|snappy|mesh) script runs with nProc > 1",
     "cause": "OpenFOAM always runs in parallel on cloudHPC.",
     "fix": "Select at least 2 vCPU on highcore/hypercore (4 on highcpu/standard/highmem).",
     "anchor": "multi-core_analysis"},
    {"id": "of_snappy", "severity": "error",
     "pattern": r"snappyHexMesh failure",
     "cause": "snappyHexMesh failed (RAM, STL geometry, settings).",
     "fix": "Read log.snappyHexMesh in the results; if it is a memory problem use more RAM.",
     "anchor": "snappyhexmesh_general_error"},
    {"id": "of_blockmesh", "severity": "error",
     "pattern": r"blockMesh failure",
     "cause": "blockMesh did not finish.",
     "fix": "Read log.blockMesh in the results (blockMeshDict vertices, blocks, patches).",
     "anchor": ""},
    {"id": "of_allboundary", "severity": "error",
     "pattern": r"allBoundary failure",
     "cause": "The generated mesh still has the default 'allBoundary' patch: the boundaries "
              "were not assigned.",
     "fix": "Check the patch names/regions in the meshing dictionaries and createPatchDict.",
     "anchor": ""},
    {"id": "of_ncc", "severity": "error",
     "pattern": r"createNonConformalCouples failure",
     "cause": "createNonConformalCouples failed.",
     "fix": "Read log.createNonConformalCouples in the results.",
     "anchor": ""},
    {"id": "of_application", "severity": "error",
     "pattern": r"application not set in system/controlDict",
     "cause": "system/controlDict has no 'application' entry, so no solver can be started.",
     "fix": "Add e.g. 'application simpleFoam;' to system/controlDict.",
     "anchor": "controldict"},
    {"id": "of_cores", "severity": "error",
     "pattern": r"required cores \[\d+\] higher than available",
     "cause": "decomposeParDict uses a method cloudHPC does not adapt and asks for more "
              "subdomains than the physical cores selected.",
     "fix": "Use method scotch or hierarchical (adapted automatically), or select vCPU on "
            "highcore/hypercore equal to numberOfSubdomains.",
     "anchor": "decomposepardict"},
    {"id": "of_multiregion", "severity": "warning",
     "pattern": r"Multi-Region cases do not have decomposeParDict adaptation",
     "cause": "Multi-region case: the region decomposeParDict files are not adapted.",
     "fix": "Set numberOfSubdomains in every region's decomposeParDict equal to the physical "
            "cores selected.",
     "anchor": "decomposepardict"},
    {"id": "of_layout", "severity": "info",
     "pattern": r"Incorrect OF dictionary - (Reconstructing folders|found)",
     "cause": "The case was not at the root of the upload; cloudHPC rearranged it.",
     "fix": "Upload 0, constant and system at the root of the case folder next time.",
     "anchor": "incorrect_dictionary"},
    {"id": "of_default_decompose", "severity": "info",
     "pattern": r"missing decomposeParDict file - using default one",
     "cause": "No system/decomposeParDict: a default one was added.",
     "fix": "Nothing to do; add your own decomposeParDict to control the method.",
     "anchor": "decomposepardict"},
    {"id": "of_incomplete_write", "severity": "info",
     "pattern": r"Incomplete file write .* Clean and reexecuting",
     "cause": "The last time step was written only partly (e.g. machine interrupted); it was "
              "removed and the run resumed automatically.",
     "fix": "Nothing to do.",
     "anchor": ""},
    {"id": "of_polymesh", "severity": "warning",
     "pattern": r"polyMesh folder not found",
     "cause": "constant/polyMesh missing: the solver has no mesh.",
     "fix": "Upload the mesh, generate it in the same run, or pass a mesh folder at launch.",
     "anchor": "general_problem_with_openfoam_solver"},
    {"id": "of_decompose", "severity": "error",
     "pattern": r"incorrect decomposeParDict file",
     "cause": "decomposeParDict not in the expected form: vCPU not applied automatically.",
     "fix": "Use method scotch (or hierarchical); template: "
            "https://github.com/CFD-FEA-SERVICE/CloudHPC/blob/master/template/OpenFOAM/system/decomposeParDict",
     "anchor": "decomposepardict"},
    {"id": "of_startfrom", "severity": "info",
     "pattern": r"suggested to use startFrom latestTime",
     "cause": "controlDict startFrom is not latestTime (changed automatically).",
     "fix": "Set 'startFrom latestTime;' in system/controlDict.",
     "anchor": "controldict"},
    # ------------------------------------------------------ other solvers
    {"id": "contam_prj", "severity": "error",
     "pattern": r"No PRJ file found",
     "cause": "CONTAM: no .prj project file in the case folder.",
     "fix": "Upload the .prj project (and the files it references) in the case folder.",
     "anchor": ""},
    {"id": "energyplus_idf", "severity": "error",
     "pattern": r"No IDF file found",
     "cause": "EnergyPlus: no .idf model in the case folder.",
     "fix": "Upload the .idf model (and the .epw weather file) in the case folder.",
     "anchor": ""},
    {"id": "liggghts_in", "severity": "error",
     "pattern": r"No IN file found",
     "cause": "LIGGGHTS: no input script whose name starts with 'in' (e.g. in.hopper).",
     "fix": "Name the LIGGGHTS input script in.<name> and upload it in the case folder.",
     "anchor": ""},
    {"id": "telemac_cas", "severity": "error",
     "pattern": r"No CAS file found",
     "cause": "openTELEMAC: no .cas steering file in the case folder.",
     "fix": "Upload the .cas steering file and the files it references in the case folder.",
     "anchor": ""},
    {"id": "calculix_inp", "severity": "error",
     "pattern": r"No INP file detected",
     "cause": "CalculiX: no .inp input file in the case folder.",
     "fix": "Upload the .inp file at the root of the case folder.",
     "anchor": ""},
    {"id": "swan_swn", "severity": "error",
     "pattern": r"No SWN file detected",
     "cause": "SWAN: no .swn input file in the case folder.",
     "fix": "Upload the .swn command file and the files it references in the case folder.",
     "anchor": ""},
    {"id": "su2_cfg", "severity": "error",
     "pattern": r"No CFG file found",
     "cause": "SU2: no .cfg configuration file in the case folder.",
     "fix": "Upload the .cfg file and the mesh it references in the case folder.",
     "anchor": ""},
    # -------------------------------------------------------- code_aster
    {"id": "ca_export", "severity": "error",
     "pattern": r"no export file detected",
     "cause": "code_aster .export file missing.",
     "fix": "Upload .export, .comm and the .med/.unv mesh (see the code_aster template).",
     "anchor": "export_file_missing"},
    {"id": "ca_serial", "severity": "info",
     "pattern": r"No parallelism detected",
     "cause": "code_aster ran as a single MPI process: the .comm has no NB_SOUS_DOMAINE or "
              "NIVEAU_PARALLELISME, so mpi_nbcpu is not used.",
     "fix": "For MPI runs use an _mpi version, add the parallel keywords to the .comm and set "
            "mpi_nbcpu in the .export.",
     "anchor": ""},
]

_COMPILED = [(e, re.compile(e["pattern"], re.IGNORECASE)) for e in CATALOG]


_MESH_RUN_RE = re.compile(r"Running\s+(blockMesh|snappyHexMesh|cfMesh|cartesianMesh|"
                          r"tetMesh|pMesh)", re.IGNORECASE)
_MESH_FAIL_RE = re.compile(r"(blockMesh|snappyHexMesh|cfMesh|cartesianMesh)\s+(failure|failed)",
                           re.IGNORECASE)


def _polymesh_entry(text: str, start: int) -> dict[str, Any] | None:
    """'polyMesh folder not found' is fine when the run then generates the mesh."""
    after = text[start:]
    ran = _MESH_RUN_RE.search(after)
    if not ran:
        return None  # nothing generated the mesh: keep the catalogue warning
    if _MESH_FAIL_RE.search(after):
        return {"severity": "warning",
                "cause": f"constant/polyMesh missing and the mesh generation in the run "
                         f"({ran.group(1)}) failed.",
                "fix": "Read the mesh log (log.blockMesh / log.snappyHexMesh / log.cartesianMesh) "
                       "in the results; see the mesh error reported below."}
    return {"severity": "info",
            "cause": f"No constant/polyMesh uploaded: the mesh was generated in the run "
                     f"({ran.group(1)}).",
            "fix": "Nothing to do."}


def diagnose(text: str) -> list[dict[str, Any]]:
    """Return catalogue entries found in a simulation output/log."""
    found = []
    for entry, rx in _COMPILED:
        m = rx.search(text or "")
        if m:
            lines = [ln.strip() for ln in (text or "").splitlines() if rx.search(ln)]
            if entry.get("matched"):   # prefer the line with the actual message
                better = [ln for ln in lines if re.search(entry["matched"], ln)]
                lines = better or lines
            line = lines[0] if lines else m.group(0)
            if entry["id"] == "of_polymesh":
                alt = _polymesh_entry(text, m.end())
                if alt:
                    entry = {**entry, **alt}
            found.append({
                "id": entry["id"], "severity": entry["severity"], "matched": line[:200],
                "cause": entry["cause"], "fix": entry["fix"],
                "docs": f"{DOCS}#{entry['anchor']}" if entry["anchor"] else None,
            })
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda f: order[f["severity"]])


# --------------------------------------------------------- pre-flight checks

INVALID_NAME_CHARS = set(",()'$~\"#*?")


def bad_name(name: str) -> list[str]:
    return sorted({c for c in name if c in INVALID_NAME_CHARS or c.isspace()})


# files kept when an FDS case starts without restart files (everything else in the
# case folder is removed before the run)
_FDS_KEEP_EXT = (".fds", ".pyrofloors", ".pyrogeom", ".bingeom", ".dat", ".bdf", ".py",
                 ".txt", ".backup", ".sh")
_UPLOAD_LEFTOVERS = ("cloudhpc", "upload.tar.gz", "cpu.csv", "ram.csv", "totcpu.csv")


def _fds_removed_files(folder: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(folder)):
        low = name.lower()
        if name.startswith(".") or low.endswith(_FDS_KEEP_EXT) or low.startswith("fds") \
                or low.startswith(_UPLOAD_LEFTOVERS) or low.endswith(".tar.gz"):
            continue
        out.append(name + ("/" if os.path.isdir(os.path.join(folder, name)) else ""))
    return out


def _read_text(path: str) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _strip_c(text: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def _block(text: str, key: str) -> str | None:
    """Content of the first 'key { ... }' block (balanced braces), or None."""
    m = re.search(rf"(?<![\w\"]){re.escape(key)}\"?\s*\{{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def _simple_residual(fv_solution: str) -> bool:
    simple = _block(_strip_c(fv_solution), "SIMPLE")
    rc = _block(simple, "residualControl") if simple else None
    return bool(rc and re.search(r"[^\s;{}]+\s+[^\s;{}]+\s*;", rc))


def _final_reltol(fv_solution: str) -> list[str]:
    solvers = _block(_strip_c(fv_solution), "solvers")
    if not solvers:
        return []
    rel, base, order = {}, {}, []
    for m in re.finditer(r'("[^"]+"|[^\s{};]+)\s*\{', solvers):
        name = m.group(1).strip('"')
        body = _block(solvers[m.start():], m.group(1)) or ""
        order.append(name)
        r = re.search(r"\brelTol\s+([^\s;]+)\s*;", body)
        if r:
            rel[name] = r.group(1)
        b = re.search(r"\$([A-Za-z_][\w.]*)\s*;", body)
        if b:
            base[name] = b.group(1)
    bad = []
    for n in order:
        if "Final" not in n:
            continue
        r = rel.get(n) or rel.get(base.get(n, ""))
        try:
            if r is not None and float(r) > 0:
                bad.append(f"{n} relTol {r}")
        except ValueError:
            pass
    return bad


def preflight(info: dict[str, Any]) -> list[dict[str, str]]:
    """Checks on a local case (output of advisor.inspect_case) before upload."""
    issues: list[dict[str, str]] = []
    folder = info["folder"]

    def add(severity, msg, anchor):
        issues.append({"severity": severity, "issue": msg,
                       "docs": f"{DOCS}#{anchor}" if anchor else None})

    chars = bad_name(info["storage_name"])
    if chars:
        add("error", f"Folder name contains characters not accepted by cloudHPC: {' '.join(chars)} "
                     "(spaces included). Rename it or pass another storage_folder.",
            "incorrect_file_or_folder_name")

    fam = info.get("family")
    if glob.glob(os.path.join(folder, "*.psm")) and fam != "fds":
        add("error", "Only a PyroSim .psm file found: export and upload the .fds file.",
            "pyrosim_input_file")

    if fam == "fds":
        fds = info.get("fds", {})
        path = os.path.join(folder, fds.get("file", ""))
        try:
            with open(path, "r", errors="replace") as f:
                text = f.read()
            order = [int(x) for x in re.findall(r"&MESH\b[^/]*?\bMPI_PROCESS\s*=\s*(\d+)", text,
                                                 re.IGNORECASE | re.DOTALL)]
            if order and order != sorted(order):
                add("error", "MPI_PROCESS values are not in ascending order: reorder the &MESH lines.",
                    "scalability_issue_with_mpi_process")
            vals = fds.get("mpi_process_values") or []
            used = [v for v in vals if v is not None]
            if used and len(used) != len(vals):
                add("error", f"MPI_PROCESS is set on {len(used)} of {len(vals)} &MESH lines: "
                             "set it on every mesh or on none.", "scalability_issue_with_mpi_process")
            if used and sorted(set(used)) != list(range(max(used) + 1)):
                missing = sorted(set(range(max(used) + 1)) - set(used))
                add("error", f"MPI_PROCESS values must start at 0 with no gaps; process(es) "
                             f"{', '.join(map(str, missing))} have no mesh.",
                    "scalability_issue_with_mpi_process")
            if re.search(r"\b(VISIBILITY|RADIATIVE HEAT FLUX|GAUGE HEAT FLUX GAS)\b", text, re.IGNORECASE):
                add("warning", "DEVC with VISIBILITY / RADIATIVE HEAT FLUX / GAUGE HEAT FLUX GAS slow "
                               "down AMD CPUs: prefer hypercpu/hypercore if delivery time matters.",
                    "devc_affecting_performances")
            if fds.get("non_ascii"):
                add("warning", "The .fds file contains non-ASCII characters (accents, symbols, "
                               "special quotes): FDS may misread them. Remove them, also from "
                               "comments and IDs.", "fds_incorrect_settings")
            if fds.get("dt_restart") is None:
                add("info", "No DT_RESTART on &DUMP: if a preemptible (non-regular) instance is "
                            "restarted, FDS starts again from the beginning. Set DT_RESTART so "
                            "restart files are written every few hours of run time (very short "
                            "intervals slow the run and fill the disk).", "")
            groups, meshes = fds.get("mpi_groups") or 0, fds.get("meshes") or 0
            if fds.get("uses_mpi_process") and 0 < groups < meshes:
                add("info", f"{meshes} meshes are grouped into {groups} MPI processes with "
                            f"MPI_PROCESS: the run uses {groups} processes, whatever the vCPU "
                            "selected. Check MPI_PROCESS if you expected one process per mesh "
                            "(GUIs may write it differently when exporting vs running).", "")
            if fds.get("has_part"):
                add("info", "&PART (particles) found: FDS scales less well with many vCPU.", "")
            if fds.get("t_end") is not None and fds.get("max_ramp_t") is not None \
                    and fds["max_ramp_t"] > fds["t_end"]:
                add("warning", f"A &RAMP goes to T={fds['max_ramp_t']:g} s but T_END is "
                               f"{fds['t_end']:g} s: check the end time.", "")
            if fds.get("meshes") == 1 and fds.get("mesh_boundary_vents"):
                add("info", "Single &MESH with &VENT MB= (mesh boundary vents): it will not be "
                            "split automatically across the cores.", "")
            restarting = bool(glob.glob(os.path.join(folder, "*.restart")))
            if not restarting:
                extra = _fds_removed_files(folder)
                if extra:
                    add("warning", f"Without .restart files the case folder is cleaned before FDS "
                                   f"starts: {', '.join(extra[:8])}{'...' if len(extra) > 8 else ''} "
                                   "would be removed. Inputs kept: .fds, .dat, .bdf, .txt, .py, .sh "
                                   "and PyroSim geometry files (.pyrofloors, .pyrogeom, .bingeom). "
                                   "Rename other files FDS needs to one of these extensions.", "")
            if not re.search(r"MINIMUM_ZONE_VOLUME", text, re.IGNORECASE):
                add("info", "Consider MINIMUM_ZONE_VOLUME=1.0 in &MISC to avoid many pressure zones.",
                    "high_number_of_pressure_zones")
        except OSError:
            pass

    if fam == "openfoam":
        if not os.path.isdir(os.path.join(folder, "constant", "polyMesh")):
            gens = [g for g, f in (("blockMesh", "blockMeshDict"), ("snappyHexMesh", "snappyHexMeshDict"),
                                   ("cfMesh", "meshDict"))
                    if os.path.exists(os.path.join(folder, "system", f))]
            if gens:
                add("info", "No constant/polyMesh: the mesh is generated in the run "
                            f"({', '.join(gens)}).", "")
            else:
                add("warning", "constant/polyMesh not found and no blockMeshDict, "
                               "snappyHexMeshDict or meshDict in system/: upload the mesh or "
                               "pass a mesh folder at launch.",
                    "general_problem_with_openfoam_solver")
        dpd = os.path.join(folder, "system", "decomposeParDict")
        if os.path.exists(dpd):
            with open(dpd, "r", errors="replace") as f:
                m = re.search(r"^\s*method\s+(\w+)\s*;", f.read(), re.MULTILINE)
            if m and m.group(1) not in ("scotch", "hierarchical"):
                add("error", f"decomposeParDict method is '{m.group(1)}': use scotch or hierarchical "
                             "so cloudHPC can set the subdomains (other methods are left as they "
                             "are and must match the physical cores selected).", "decomposepardict")
        if glob.glob(os.path.join(folder, "system", "*", "decomposeParDict")):
            add("warning", "Multi-region case: the region decomposeParDict files are not adapted "
                           "automatically; numberOfSubdomains must equal the physical cores "
                           "selected.", "decomposepardict")
        if os.path.exists(os.path.join(folder, "Allrun")):
            add("info", "Allrun found: it replaces cloudHPC's standard meshing/solving sequence "
                        "(decomposeParDict is still adapted first).", "")
        orig = [d for d in ("0.orig", "0.org") if os.path.isdir(os.path.join(folder, d))]
        if orig and os.path.isdir(os.path.join(folder, "0")):
            add("info", f"Both 0/ and {orig[-1]}/ exist: 0/ is replaced by {orig[-1]}/ at start.", "")
        cd = os.path.join(folder, "system", "controlDict")
        if os.path.exists(cd):
            with open(cd, "r", errors="replace") as f:
                m = re.search(r"^\s*startFrom\s+(\w+)\s*;", f.read(), re.MULTILINE)
            with open(cd, "r", errors="replace") as f:
                cd_text = f.read()
            if m and m.group(1) != "latestTime":
                add("info", f"startFrom is '{m.group(1)}': cloudHPC sets it to latestTime.",
                    "controldict")
            if not re.search(r"^\s*application\s+\S+\s*;", cd_text, re.MULTILINE) \
                    and not os.path.exists(os.path.join(folder, "Allrun")):
                add("warning", "system/controlDict has no 'application' entry: the solver run "
                               "stops unless you only generate the mesh with a mesher entry.",
                    "controldict")
        if not (os.path.isdir(os.path.join(folder, "0")) or os.path.isdir(os.path.join(folder, "0.orig"))):
            add("warning", "No 0/ (or 0.orig/) folder with initial conditions.", "incorrect_dictionary")

    if fam == "openfoam":
        logs = sorted(os.path.basename(x) for x in glob.glob(os.path.join(folder, "log.*")))
        if logs and (os.path.exists(os.path.join(folder, "Allrun"))
                     or os.path.exists(os.path.join(folder, "Allrun.pre"))):
            add("warning", f"Existing log files ({', '.join(logs[:6])}"
                           f"{'...' if len(logs) > 6 else ''}): Allrun (runApplication) skips "
                           "every step that already has a log, so meshing/solving may not run. "
                           "Ask the user before removing them.", "general_problem_with_openfoam_solver")

    leftovers = [f for f in ("cloudhpc.log", "cloudhpc.err", "cpu.csv", "ram.csv", "totcpu.csv")
                 if os.path.exists(os.path.join(folder, f))]
    leftovers += [os.path.basename(x) for x in glob.glob(os.path.join(folder, "*.tar.gz"))]
    if leftovers:
        add("info", f"Files from a previous cloudHPC run are in the folder ({', '.join(leftovers[:6])}"
                    f"{'...' if len(leftovers) > 6 else ''}): they are uploaded too. Harmless, "
                    "but a clean copy of the case uploads faster.", "incorrect_compressed_file")

    many = info.get("input_files") or []
    if fam in ("contam", "energyplus", "swan", "su2") and len(many) > 1:
        add("warning", f"{len(many)} input files found ({', '.join(many[:5])}): only the first "
                       f"in alphabetical order ({many[0]}) is run. Keep one per case folder.", "")

    if fam == "energyplus" and info.get("hvac_templates") and not glob.glob(os.path.join(folder, "*.imf")):
        add("error", "The model uses HVACTemplate objects, which are not expanded for a plain .idf: "
                     "expand the model locally (ExpandObjects) before uploading.", "")
    if fam == "energyplus" and info.get("input_files") and info.get("idf_version") is None:
        add("warning", "No Version object found in the IDF: add it (it must match the solver "
                       "version you launch).", "")
    if fam == "energyplus" and not glob.glob(os.path.join(folder, "*.epw")):
        add("info", "No .epw weather file: only design days can be simulated, not an annual run.", "")

    if fam == "telemac":
        cas = {c: _read_text(os.path.join(folder, c)) for c in many}
        coupled = {os.path.basename(m.strip().strip("'\"")).lower()
                   for t in cas.values()
                   for m in re.findall(r"STEERING FILE\s*[=:]\s*(\S+)", t, re.I)}
        mains = [c for c in cas if c.lower() not in coupled]
        if not mains:
            add("error", "Every .cas file is referenced by another as a coupled steering file: "
                         "no main case to run.", "")
        for c in mains:
            low, t = c.lower(), cas[c].upper()
            if low.startswith(("t3d_", "t2d_", "tom_", "art_")):
                continue
            if re.search(r"NUMBER OF HORIZONTAL LEVELS|3D RESULT FILE|NON-HYDROSTATIC VERSION", t) \
                    or re.search(r"NUMBER OF (DIRECTIONS|FREQUENCIES)", t):
                continue
            add("warning", f"{c}: module not recognised from the name or keywords, it will be "
                           "run with TELEMAC-3D. Rename it t2d_*.cas for TELEMAC-2D, tom_*.cas "
                           "for TOMAWAC or art_*.cas for ARTEMIS.", "")
        for c in mains:
            if re.search(r"\s", c):
                add("warning", f"The file name '{c}' contains spaces: TELEMAC may not read it. "
                               "Rename it without spaces.", "")
        if len(mains) > 1:
            add("info", f"{len(mains)} main .cas files ({', '.join(mains[:5])}): they run one "
                        "after another, each on all cores.", "")

    if fam == "liggghts":
        ins = info.get("input_files") or []
        if len(ins) > 1:
            add("error", f"Several files start with 'in' ({', '.join(ins[:5])}): the input "
                         "script must be the only one, otherwise another file may be read as "
                         "the script. Rename the others (e.g. inlet.stl -> mesh_inlet.stl).", "")

    if fam == "openlb":
        exe = info.get("executables") or []
        if exe:
            add("error", f"Executable files at the folder root ({', '.join(exe[:5])}): the case "
                         "is compiled and the single executable produced is run, so other "
                         "executables must be removed (or their execute permission removed).", "")

    if fam == "dafoam":
        entry = info.get("dafoam_entry") or []
        if not ({"runScript.py", "Allrun"} & set(entry)):
            add("error", "DAFoam needs runScript.py (with preProcessing.sh) or an Allrun script.", "")
        if {"runScript.py", "Allrun"} <= set(entry):
            add("warning", "Both runScript.py and Allrun are present: cloudHPC runs BOTH "
                           "(preProcessing.sh, runScript.py, then Allrun). Keep only the one you "
                           "need.", "")
        if info.get("number_of_subdomains") is None:
            add("warning", "numberOfSubdomains not found in system/decomposeParDict: for DAFoam "
                           "it must be set by you and match the physical cores.", "")

    if fam == "openradioss":
        names = info.get("input_files") or []
        keys = [n for n in names if n.endswith(".key")]
        starters = [n for n in names if n.endswith("_0000.rad")]
        engines = [n for n in names if n.endswith(".rad") and not n.endswith("_0000.rad")]
        if keys and starters:
            add("warning", f"Both LS-DYNA (.key) and Radioss (_0000.rad) inputs are present: the "
                           f".key file ({keys[0]}) is used.", "")
        if not keys and not starters:
            add("error", "No OpenRadioss starter (*_0000.rad) or LS-DYNA (.key) input found.", "")
        if starters and not keys and not engines:
            add("error", "Starter *_0000.rad found but no engine file (*_0001.rad).", "")

    if fam == "calculix" and len(many) > 1:
        add("warning", f"{len(many)} .inp files found ({', '.join(many[:5])}): only the first in "
                       f"alphabetical order ({many[0]}) is run. If the others are *INCLUDE files, "
                       "give them another extension (e.g. .msh, .nam) or make sure the main file "
                       "sorts first.", "")

    if fam == "codesaturne" and info.get("saturne_case") is None:
        add("error", "No code_saturne case (a folder with DATA and SRC) found at the root or one "
                     "level down.", "")

    if fam == "xbeach" and not os.path.exists(os.path.join(folder, "params.txt")):
        add("error", "XBeach needs params.txt at the root of the case folder.", "")
    if fam == "xbeach" and info.get("nonhydrostatic"):
        add("info", "Non-hydrostatic case (wavemodel = nonh): XBeach runs on ONE MPI process "
                    "on cloudHPC (more processes create spurious waves at the subdomain "
                    "boundaries). Select 2 vCPU.", "")

    if fam == "custom":
        py = [f for f in info.get("scripts", []) if f.endswith(".py")]
        if py and not info.get("has_requirements"):
            add("info", "Python scripts without requirements.txt: only the standard library "
                        "(and preinstalled system packages) will be available.", "")

    if fam == "precice":
        pc = info.get("precice", {})
        parts, folders = pc.get("participants", []), pc.get("folders", {})
        if len(parts) < 2:
            add("error", "precice-config.xml defines fewer than two participants.", "")
        for p in parts:
            got = folders.get(p, [])
            if not got:
                add("error", f"Participant '{p}' has no supported folder (OpenFOAM: "
                             "system/controlDict + system/preciceDict with 'participant "
                             f"{p};'; CalculiX: config.yml + .inp with *STEP; code_aster: "
                             ".export + .comm using precice_aster; FDS: one .fds with "
                             f"&PRCI PARTICIPANT='{p}').", "")
            elif len(got) > 1:
                add("error", f"Participant '{p}' is provided by several folders ({', '.join(got)}): "
                             "keep only the one to run.", "")
        for d, kind in pc.get("solver_of_folder", {}).items():
            path = os.path.join(folder, d)
            if kind == "openfoam" and not (glob.glob(os.path.join(path, "constant", "polyMesh", "faces*"))
                                           or os.path.exists(os.path.join(path, "system", "blockMeshDict"))
                                           or os.path.exists(os.path.join(path, "system", "snappyHexMeshDict"))):
                add("error", f"{d}: no mesh (constant/polyMesh) and no blockMeshDict or "
                             "snappyHexMeshDict to generate it.", "")
            if kind == "calculix":
                inps = glob.glob(os.path.join(path, "*.inp"))
                if not any(re.search(r"^\*STEP", _read_text(i), re.M | re.I) for i in inps):
                    add("error", f"{d}: no .inp file with *STEP.", "")
        max_time = pc.get("max_time")
        for d, f in (pc.get("fds") or {}).items():
            if f.get("several"):
                add("error", f"{d}: several .fds files with a &PRCI line "
                             f"({', '.join(f['several'])}): keep only the one to run.", "")
                continue
            if pc.get("implicit"):
                add("error", f"{d}: the FDS adapter supports explicit coupling only "
                             "(serial-explicit or parallel-explicit); precice-config.xml uses "
                             "an implicit scheme.", "")
            if f.get("mult_id"):
                add("error", f"{d}: {f['file']} uses MULT_ID on &MESH: write one explicit &MESH "
                             "line per mesh (one MPI process each).", "")
            if not f.get("meshes"):
                add("error", f"{d}: no &MESH line in {f['file']}.", "")
            cf = os.path.normpath(os.path.join(d, f.get("config_file") or "precice-config.xml"))
            if cf != "precice-config.xml":
                add("error", f"{d}: CONFIG_FILE='{f.get('config_file')}' on &PRCI does not point to "
                             "precice-config.xml in the case folder: use "
                             "CONFIG_FILE='../precice-config.xml'.", "")
            t_end = f.get("t_end")
            if max_time is not None and t_end is not None:
                if t_end < max_time:
                    add("warning", f"{d}: T_END {t_end:g} is lower than max-time {max_time:g} of "
                                   "precice-config.xml: FDS ends first and the run is stopped as "
                                   f"failed. Set T_END={max_time:g}.", "")
                elif t_end > max_time:
                    add("info", f"{d}: T_END {t_end:g} is higher than max-time {max_time:g}: FDS "
                                "stops when the coupling ends.", "")
        for d, kind in pc.get("solver_of_folder", {}).items():
            if kind != "openfoam":
                continue
            fv = _read_text(os.path.join(folder, d, "system", "fvSolution"))
            if _simple_residual(fv):
                add("warning", f"{d}: SIMPLE in system/fvSolution has residualControl: OpenFOAM "
                               "stops when converged, possibly before the coupling end time, and "
                               "the run would then be stopped as failed. Remove residualControl "
                               "to couple until the end.", "")
            ctl = _strip_c(_read_text(os.path.join(folder, d, "system", "controlDict")))
            m = re.search(r"^\s*endTime\s+([-+0-9.eE]+)\s*;", ctl, re.M)
            try:
                et = float(m.group(1)) if m else None
            except ValueError:
                et = None
            if max_time is not None and et is not None and et < max_time:
                add("warning", f"{d}: endTime {et:g} in system/controlDict is lower than max-time "
                               f"{max_time:g} of precice-config.xml: this participant stops "
                               "before the coupling ends.", "")
            if pc.get("implicit"):
                bad = _final_reltol(fv)
                if bad:
                    add("warning", f"{d}: implicit coupling with relTol > 0 on the final solvers "
                                   f"({', '.join(bad)}): the coupling iterations may never "
                                   "converge. Use relTol 0 on the *Final solvers.", "")
        if pc.get("unnamed_code_aster"):
            add("error", "Cannot tell which participant the code_aster folder(s) "
                         f"{', '.join(pc['unnamed_code_aster'])} provide: add "
                         "participant=\"<name>\" to the run_fsi / run_cht call in the .comm.", "")
        for d, kind in pc.get("solver_of_folder", {}).items():
            if kind == "codeaster":
                exports = glob.glob(os.path.join(folder, d, "*.export"))
                if not any(re.search(r"^F\s+comm\s", _read_text(e), re.M) for e in exports):
                    add("error", f"{d}: no .export with an 'F comm' line.", "")
        if pc.get("ignored_folders"):
            add("info", "Ignored folders: " + "; ".join(pc["ignored_folders"]), "")
        if pc.get("absolute_paths"):
            add("info", "precice-config.xml uses absolute directories: relative paths as in "
                        "the tutorials are safer (cloudHPC makes them absolute when code_aster "
                        "takes part).", "")
        if os.path.exists(os.path.join(folder, "run.sh")) or glob.glob(os.path.join(folder, "*", "run.sh")):
            add("info", "run.sh / clean.sh scripts are not executed on cloudHPC: settings made "
                        "only there are not applied.", "")

    if fam == "lammps":
        if not info.get("main_inputs"):
            add("error", "No LAMMPS input with a run, minimize or rerun command.", "")
        if info.get("missing_files"):
            add("error", "Files read by the input are missing from the folder: "
                         + ", ".join(info["missing_files"][:8]), "")
        if info.get("undefined_variables"):
            add("warning", "Variables used but not defined with 'variable' in the inputs: "
                           + ", ".join(info["undefined_variables"][:8])
                           + ". Command-line variables (-var) are not passed on cloudHPC: "
                             "define them in the input.", "")

    if fam == "palabos":
        for c in info.get("cases", []):
            if c["reads_argument"] and len(c["xml_files"]) > 1:
                add("error", f"{c['main']} reads a parameter file but its folder has several "
                             f".xml files ({', '.join(c['xml_files'])}): keep only one.", "")
            elif c["reads_argument"] and not c["xml_files"]:
                add("warning", f"{c['main']} reads a command-line argument but there is no .xml "
                               "parameter file next to it: it runs without arguments.", "")
        if os.path.exists(os.path.join(folder, "CMakeLists.txt")) or os.path.exists(os.path.join(folder, "Makefile")):
            add("info", "CMakeLists.txt / Makefile are not used: cloudHPC compiles each .cpp with "
                        "main() against Palabos 2.3.0.", "")

    if fam == "kratos":
        for c in info.get("kratos_cases", []):
            where = "" if c["folder"] == "." else f"{c['folder']}: "
            for sev, msg in c["issues"]:
                add(sev, where + msg, "")

    if fam == "freefem":
        if not info.get("main_scripts"):
            add("error", "Every .edp is included by another one: no main script to run.", "")
        for e in info.get("reads_keyboard", []):
            add("error", f"{e} reads from the keyboard (cin >>): there is no keyboard input on "
                         "cloudHPC, set the value in the script.", "")

    if fam == "tflows":
        for c in info.get("tflows_cases", []):
            where = "" if c["folder"] == "." else f"{c['folder']}: "
            if not c["problem_name"]:
                add("error", f"{where}PROBLEM_NAME missing in control.", "")
            elif not (c["grid_ready"] or c["generate_scripts"] or c["convert_scripts"]
                      or c["grid_archives"]):
                add("error", f"{where}no grid: expected {c['problem_name']}.cfn/.dim, "
                             "generate*.scr (+ .dom) or convert*.scr (+ mesh file).", "")
            if c["user_mod"]:
                add("warning", f"{where}User_Mod is not compiled on cloudHPC: the standard "
                               "Process runs without your user functions.", "")
            if not c["backup_interval"]:
                add("info", f"{where}no BACKUP_SAVE_INTERVAL in control: a stopped or "
                            "preempted run cannot be resumed.", "")

    if fam == "dualsphysics" and info.get("user_scripts"):
        add("info", "Shell scripts (e.g. xCase*.sh) are not executed: put solver parameters in "
                    "<execution><parameters> of the _Def.xml.", "")

    if fam == "phasicflow":
        from .advisor import _PFP_SOLVERS
        for c in info.get("phasicflow_cases", []):
            where = "" if c["folder"] == "." else f"{c['folder']}: "
            if not c["coupling"]:
                add("error", f"{where}constant/couplingProperties not found: PhasicFlowPlus runs "
                             "coupled CFD-DEM cases only.", "")
            if c["application"] not in _PFP_SOLVERS:
                add("error", f"{where}application in system/controlDict is "
                             f"'{c['application'] or 'missing'}': set one of "
                             f"{', '.join(_PFP_SOLVERS)}.", "")
            if not (c["mesh"] or c["block_mesh"] or len(c["msh"]) == 1):
                add("error", f"{where}" + (f"several .msh files ({', '.join(c['msh'])}): keep one."
                                           if c["msh"] else
                                           "no mesh: provide constant/polyMesh, "
                                           "system/blockMeshDict or one Fluent .msh file "
                                           "(mesh scripts such as mesh/Allmesh are not run)."), "")
            try:
                cfd, dem = float(c["cfd_start"]), float(c["dem_start"])
            except ValueError:
                cfd = dem = None
            if cfd is not None and cfd > dem:
                try:
                    if c["dem_end"] is not None and abs(float(c["dem_end"]) - cfd) > 1e-9:
                        add("warning", f"{where}settings/settingsDict endTime ({c['dem_end']}) "
                                       f"differs from controlDict startTime ({c['cfd_start']}): "
                                       "the DEM-only stage should end where CFD-DEM starts.", "")
                except ValueError:
                    pass
            tdir = os.path.join(folder, c["folder"], c["cfd_start"])
            if not c["fluid_u"] and not glob.glob(os.path.join(tdir, "U*")):
                add("error", f"{where}no initial fluid velocity U: put the initial fields in "
                             f"FluidField/ or in the {c['cfd_start']}/ folder.", "")
            if c["user_scripts"]:
                add("info", f"{where}{', '.join(c['user_scripts'])} are not executed: cloudHPC "
                            "runs the solver steps itself.", "")

    if fam == "code_aster":
        nb = info.get("mpi_nbcpu")
        if nb and nb > 1 and not info.get("comm_parallel"):
            add("warning", f"mpi_nbcpu is {nb} but the .comm has no NB_SOUS_DOMAINE or "
                           "NIVEAU_PARALLELISME: the run uses a single MPI process.", "")
        present = set(os.listdir(folder))
        missing = [f for f in info.get("export_inputs", []) if f not in present]
        if missing:
            add("error", f"Files referenced in the .export are not in the case folder: "
                         f"{', '.join(missing[:6])}. Paths in the .export are reduced to the "
                         "file name, so every input must be at the root of the case folder.",
                "export_file_missing")
        if not glob.glob(os.path.join(folder, "*.export")):
            add("error", "No .export file: code_aster needs .export, .comm and .med/.unv.",
                "export_file_missing")
        if not (glob.glob(os.path.join(folder, "*.med")) or glob.glob(os.path.join(folder, "*.unv"))
                or glob.glob(os.path.join(folder, "attachments", "*.med"))):
            add("warning", "No .med or .unv mesh found in the folder.", "code_aster_settings")

    return issues


# ------------------------------------------------ solver logs after download

def check_solver_logs(folder: str) -> list[dict[str, str]]:
    """Check the solver's own logs in downloaded results.

    OpenFOAM: every log.* must end with 'End' and contain no FOAM FATAL error.
    FDS: <CHID>.out must contain 'FDS completed successfully'.
    """
    checks: list[dict[str, str]] = []

    def tail(path: str, n: int = 8000) -> str:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(f.tell() - n, 0))
                return f.read().decode(errors="replace")
        except OSError:
            return ""

    def grep(path: str, pattern: str) -> str | None:
        rx = re.compile(pattern)
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if rx.search(line):
                        return line.strip()[:200]
        except OSError:
            pass
        return None

    for log in sorted(glob.glob(os.path.join(folder, "log.*"))):
        name = os.path.basename(log)
        fatal = grep(log, r"FOAM FATAL (IO )?ERROR")
        ended = re.search(r"^\s*End\s*$", tail(log), re.MULTILINE) is not None
        if fatal:
            checks.append({"file": name, "status": "error", "detail": fatal})
        elif ended:
            checks.append({"file": name, "status": "ok", "detail": "ends with 'End'"})
        else:
            checks.append({"file": name, "status": "warning",
                           "detail": "no final 'End': the step stopped early or was interrupted"})

    for out in sorted(glob.glob(os.path.join(folder, "*.out"))):
        text = tail(out, 20000)
        if "Fire Dynamics Simulator" not in text and "FDS" not in text:
            continue
        name = os.path.basename(out)
        if "FDS completed successfully" in text:
            checks.append({"file": name, "status": "ok", "detail": "FDS completed successfully"})
        else:
            err = grep(out, r"ERROR|Numerical Instability|STOP:")
            checks.append({"file": name, "status": "error" if err else "warning",
                           "detail": err or "no 'FDS completed successfully' line"})
    return checks

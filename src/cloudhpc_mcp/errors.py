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


def diagnose(text: str) -> list[dict[str, Any]]:
    """Return catalogue entries found in a simulation output/log."""
    found = []
    for entry, rx in _COMPILED:
        m = rx.search(text or "")
        if m:
            line = next((ln.strip() for ln in (text or "").splitlines() if rx.search(ln)), m.group(0))
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
            add("warning", "constant/polyMesh not found: generate the mesh in the run "
                           "(snappyHexMesh/cfMesh) or pass a mesh folder at launch.",
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
    if fam in ("contam", "energyplus", "telemac", "swan", "su2") and len(many) > 1:
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
        add("info", "This solver entry runs TELEMAC-3D: a TELEMAC-2D steering file will not run.", "")

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

    if fam == "custom":
        py = [f for f in info.get("scripts", []) if f.endswith(".py")]
        if py and not info.get("has_requirements"):
            add("info", "Python scripts without requirements.txt: only the standard library "
                        "(and preinstalled system packages) will be available.", "")

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

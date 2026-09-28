"""How to prepare a case for each solver on cloudHPC.

User-facing descriptions of what cloudHPC expects and does for each solver.
Written for users: no implementation details of the platform.

Parallel terms used below:
  physical cores = vCPU on highcore/hypercore, vCPU / 2 on the hyperthreaded
  types (highcpu, standard, highmem, hypercpu).
"""

from __future__ import annotations

from typing import Any

DOCS = "https://docs.cloudhpc.cloud"

GUIDES: dict[str, dict[str, Any]] = {
    "fds": {
        "name": "FDS (Fire Dynamics Simulator)",
        "input": ["One .fds file in the case folder (PyroSim .psm files are not accepted: "
                  "export the .fds)."],
        "automatic": [
            "A single &MESH with at least 40,000 cells is split automatically when 4 or more "
            "vCPU are selected.",
            "Results are packed in FDS.tar.gz in the case folder.",
        ],
        "parallel": "One MPI process per &MESH (or per MPI_PROCESS group): vCPU = groups x 2 "
                    "on highcpu/standard/highmem/hypercpu, vCPU = groups on highcore/hypercore.",
        "resources": "Start on highcpu; move to standard, then highmem, only after a memory error.",
        "logs": ["<CHID>.out", "<CHID>.log"],
        "docs": f"{DOCS}/scalability/#fds",
    },
    "openfoam": {
        "name": "OpenFOAM (incl. snappyHexMesh, cfMesh)",
        "input": ["A case folder with 0/ (or 0.orig/), constant/ and system/ at its root; "
                  "optionally an Allrun script."],
        "automatic": [
            "numberOfSubdomains in system/decomposeParDict is set to match the vCPU "
            "(method scotch or hierarchical).",
            "startFrom is set to latestTime in system/controlDict.",
        ],
        "parallel": "Always parallel (MPI): at least 2 vCPU, on highcore or hypercore.",
        "resources": "At least 50,000 cells per core; highcore or hypercore only.",
        "logs": ["log.<application>"],
        "docs": f"{DOCS}/scalability/#openfoam",
    },
    "dafoam": {
        "name": "DAFoam (DAFoam-v5.0.0 on OpenFOAM v2506, DAFoam-turbo on OpenFOAM v1812)",
        "input": [
            "Either preProcessing.sh + runScript.py (the usual DAFoam tutorial layout), or an "
            "Allrun script, at the root of the case folder, together with the OpenFOAM case "
            "(0/, constant/, system/).",
        ],
        "automatic": [
            "The scripts are made executable, so file permissions from Windows are not a problem.",
            "Run order: preProcessing.sh (if present), then runScript.py in parallel (if "
            "present), then Allrun (if present). If both runScript.py and Allrun exist, "
            "BOTH are run: keep only the one you need.",
            "runScript.py is started with one MPI process per physical core.",
            "OpenMDAO report files are disabled to save disk space and time.",
            "Unlike the plain OpenFOAM solvers, decomposeParDict is NOT adjusted: "
            "numberOfSubdomains must equal the number of physical cores you select.",
        ],
        "parallel": "MPI, one process per physical core: use highcore or hypercore "
                    "(numberOfSubdomains = vCPU).",
        "resources": "Adjoint optimisation needs much more RAM than a plain CFD run: if it fails "
                     "for memory, add vCPU (and subdomains) rather than changing machine type.",
        "logs": ["the output of preProcessing.sh / runScript.py / Allrun in the simulation log"],
        "docs": "https://dafoam.github.io",
    },
    "calculix": {
        "name": "CalculiX",
        "input": ["A .inp file (plus any *INCLUDE files) in the case folder."],
        "automatic": ["Results are packed in CALCULIX.tar.gz."],
        "parallel": "Threads (PARDISO/PaStiX versions) or MPI (-MPI versions).",
        "resources": "PARDISO scales up to about 200,000 nodes per core.",
        "logs": [],
        "docs": f"{DOCS}/scalability/#calculix",
    },
    "code_aster": {
        "name": "code_aster",
        "input": [".export, .comm and the .med/.unv mesh in the case folder."],
        "automatic": ["ncpus in the .export is set to use the remaining vCPU as OpenMP threads; "
                      "mpi_nbcpu (MPI processes) is yours to choose."],
        "parallel": "MPI (versions with _mpi) + OpenMP: 2 threads per MPI process, at most 4.",
        "resources": "Memory hungry: start on highcpu, then standard, then highmem.",
        "logs": [],
        "docs": f"{DOCS}/scalability/#code_aster",
    },
    "openradioss": {
        "name": "OpenRadioss",
        "input": [
            "Radioss format: a starter file *_0000.rad and one or more engine files "
            "*_0001.rad, *_0002.rad ... (the engine files are run one after the other), or",
            "LS-DYNA format: a .key file (a file named *OpenRadioss.key is preferred if "
            "there are several).",
            "If both a .key and a *_0000.rad are present, the .key file is used.",
        ],
        "automatic": [
            "Spaces in .rad and .key file names are replaced with underscores.",
            "Animation files are converted to VTK (.vtk) at the end, ready for ParaView.",
        ],
        "parallel": "MPI (one process per physical core) + OpenMP (one thread per hardware "
                    "thread of the core), so hyperthreaded machines are used too.",
        "resources": "About 20,000-30,000 elements per core; hypercore gave the best "
                     "cost/performance in our benchmark.",
        "logs": ["OpenRadiossSTARTER.log", "OpenRadiossENGINE_<file>.log"],
        "docs": f"{DOCS}/scalability/#openradioss",
    },
    "contam": {
        "name": "CONTAM 3.4",
        "input": ["One .prj project file in the case folder (with several, only the first in "
                  "alphabetical order is run), plus any files it references (weather, "
                  "contaminant, schedule files) in the same folder."],
        "automatic": [],
        "parallel": "Serial: CONTAM uses a single core, extra vCPU are not used.",
        "resources": "1 vCPU on standard (or 2 vCPU on highcpu) is enough for most projects.",
        "logs": ["contam.log"],
        "docs": "https://www.nist.gov/el/energy-and-environment-division-73200/nist-multizone-modeling",
    },
    "energyplus": {
        "name": "EnergyPlus (9.4.0, 9.6.0, 25.2.0)",
        "input": [
            "One .idf model (with several, only the first in alphabetical order is run).",
            "Optional: a .epw weather file (without it only design days can run), a custom "
            ".idd, or a .imf macro file (EP-Macro is then run and the model expanded).",
        ],
        "automatic": [
            "Spaces in .idf, .epw, .idd and .imf file names are replaced with underscores.",
            "Versions 9.6.0 and 25.2.0 run multi-threaded on all vCPU; 9.4.0 runs on one thread.",
        ],
        "parallel": "Multi-threaded (9.6.0 and later); a single simulation rarely benefits from "
                    "more than a few threads.",
        "resources": "2-4 vCPU on highcpu are enough for most buildings.",
        "logs": ["energyplus.log", "eplusout.err (EnergyPlus errors and warnings)"],
        "docs": "https://energyplus.net/documentation",
    },
    "liggghts": {
        "name": "LIGGGHTS 3.8.0 (DEM)",
        "input": [
            "The input script must be the only file whose name starts with 'in' (e.g. "
            "in.hopper): other files starting with 'in' (e.g. inlet.stl, inflow.txt) can be "
            "picked instead. Put meshes and data files next to it and reference them with "
            "relative paths.",
        ],
        "automatic": [],
        "parallel": "MPI, one process per physical core: use highcore or hypercore. The domain "
                    "decomposition follows your 'processors' command (or LIGGGHTS defaults).",
        "resources": "Scaling depends on particle count: keep tens of thousands of particles per "
                     "core as a starting point.",
        "logs": ["LIGGGHTS.log"],
        "docs": "https://www.cfdem.com",
    },
    "openlb": {
        "name": "OpenLB (1.7r0, 1.8r1)",
        "input": [
            "The case source: a Makefile and the .cpp (and .h) files, as in the OpenLB "
            "examples.",
            "The folder must not contain other executable files at its root (e.g. shell "
            "scripts with execute permission or precompiled binaries).",
        ],
        "automatic": [
            "OLB_ROOT in your Makefile is set to the cloudHPC OpenLB installation, so you can "
            "keep your local path.",
            "The case is compiled with make and the resulting executable is run.",
        ],
        "parallel": "The executable is started directly (no MPI launcher): it runs as a single "
                    "process, parallel only if built with OpenMP in the OpenLB configuration.",
        "resources": "Start with a few vCPU; more vCPU help only with an OpenMP build.",
        "logs": ["the compiler and program output in the simulation log"],
        "docs": "https://www.openlb.net/documentation/",
    },
    "telemac": {
        "name": "openTELEMAC v8p5r1 (TELEMAC-3D)",
        "input": [
            "One .cas steering file (with several, only the first in alphabetical order is "
            "run) plus the geometry, boundary-condition and other files it references, in the "
            "same folder.",
            "The case is run with TELEMAC-3D: 2D (TELEMAC-2D) steering files are not run by "
            "this solver entry.",
        ],
        "automatic": ["The number of parallel partitions (ncsize) is set to the number of "
                      "physical cores."],
        "parallel": "MPI, one process per physical core: use highcore or hypercore.",
        "resources": "Keep several thousand mesh nodes per core; very small meshes do not "
                     "benefit from many cores.",
        "logs": ["openTELEMAC.log"],
        "docs": "https://www.opentelemac.org",
    },
}

# script-name prefixes (lower case) -> guide key, for families not in advisor
EXTRA_FAMILIES = {
    "dafoam": "dafoam",
    "contam": "contam",
    "energyplus": "energyplus",
    "liggghts": "liggghts",
    "openlb": "openlb",
    "opentelemac": "telemac",
    "telemac": "telemac",
}


def guide_for(family: str) -> dict[str, Any] | None:
    return GUIDES.get(family)

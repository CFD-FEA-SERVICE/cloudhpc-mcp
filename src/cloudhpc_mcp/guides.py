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
        "name": "FDS (Fire Dynamics Simulator), versions 6.7.x to 6.11.x",
        "input": [
            "One .fds file in the case folder (.FDS is accepted too). With several, only the "
            "first in alphabetical order is run. PyroSim .psm files are not accepted: export "
            "the .fds.",
            "Keep the file plain ASCII: accents, symbols or special quotes (also in comments "
            "and IDs) trigger a warning and may be misread by FDS.",
            "Auxiliary inputs must have one of these extensions to be kept: .dat, .bdf, .txt, "
            ".py, .sh, or PyroSim geometry files (.pyrofloors, .pyrogeom, .bingeom). On a new "
            "run (no .restart files in the folder) every other file is removed before FDS "
            "starts.",
        ],
        "automatic": [
            "Spaces in the .fds file name are replaced with underscores.",
            "Line breaks inside &MESH lines written by BlenderFDS and Windows line endings "
            "are fixed; 'mpi_process' written in lower or mixed case is corrected.",
            "Any .stop file left in the folder (e.g. from a previous soft stop) is removed "
            "at start: nothing to do before relaunching.",
            "Single &MESH: it is split automatically into one piece per physical core, with "
            "at least ~15,000 cells and 12 cells per direction per piece (the split must "
            "divide the IJK counts exactly). Not done if the mesh uses MULT_ID or "
            "MPI_PROCESS, or if the case has &VENT with MB=. The original file is kept as "
            ".fds.backup.",
            "One MPI process per &MESH (or per MPI_PROCESS group); the remaining vCPU are "
            "used as OpenMP threads.",
            "On GPU instances the MPI processes are shared among the available GPUs.",
            "Checks printed in the log: MPI_PROCESS not in ascending order (error), more "
            "meshes than vCPU (error), MULT_ID on &MESH, &PART particles, a &RAMP longer "
            "than T_END, too many threads per mesh, DEVC quantities that are slow on AMD "
            "(VISIBILITY, RADIATIVE HEAT FLUX, GAUGE HEAT FLUX GAS).",
            "Every .csv output is also converted to .xlsx; results are packed in FDS.tar.gz.",
        ],
        "restart": [
            "To resume a stopped run, launch it again in the SAME storage folder. If .restart "
            "files are present, RESTART=.TRUE. is added to &MISC automatically (a &MISC line "
            "is created if missing): do not edit the file and do not delete any .stop file.",
            "FDS writes .restart files only if DT_RESTART is set on &DUMP: set it for long "
            "runs.",
            "Without .restart files the run starts from the beginning and previous outputs "
            "are removed.",
            "Relaunch with the same vCPU and RAM type: a different mesh split discards the "
            "restart files.",
            "FDS+EVAC cases cannot be restarted.",
        ],
        "parallel": "One MPI process per &MESH or MPI_PROCESS group. Minimum: vCPU >= groups "
                    "(otherwise 'low vCPU selected'). Best speed: vCPU = groups x 2 on "
                    "highcpu/standard/highmem/hypercpu, vCPU = groups on highcore/hypercore.",
        "resources": "Start on highcpu; move to standard, then highmem, only after a memory "
                     "error. Never 1 vCPU on highcpu.",
        "logs": ["<CHID>.log (solver output)", "<CHID>.out", "fds-mesh.csv (cells per MPI process)"],
        "docs": f"{DOCS}/scalability/#fds",
    },
    "openfoam": {
        "name": "OpenFOAM (openfoam.org 5-14, openfoam.com v1706-v2606, foam-extend 5.0) incl. "
                "snappyHexMesh and cfMesh",
        "input": [
            "A case folder with 0/ (or 0.orig/), constant/ and system/ at its root. If the "
            "case is one level down, or the dictionaries are all at the root, it is "
            "rearranged automatically (warning in the log).",
            "system/controlDict must contain 'application <solver>;'.",
            "Optional: an Allrun script. If present it runs INSTEAD of the standard sequence "
            "below (log in Allrun.log); decomposeParDict is still adapted first.",
            "Custom code: an Allwmake at the root, or sub-folders with Allwmake or Make/, are "
            "compiled automatically before the run (logs *_compilation.log).",
        ],
        "automatic": [
            "decomposeParDict: added if missing (scotch). With method scotch or hierarchical "
            "numberOfSubdomains (and the hierarchical n coefficients) are set to the physical "
            "cores. Other methods are left unchanged: the run stops if they ask for more "
            "subdomains than physical cores. Multi-region cases (system/<region>/"
            "decomposeParDict) are not adapted.",
            "Mesh: if constant/polyMesh is missing (or a mesher entry such as snappyHexMesh-"
            "or cfMesh- is launched), the mesh is generated: blockMesh if blockMeshDict "
            "exists, then snappyHexMesh in parallel if snappyHexMeshDict exists (feature edges "
            "extracted first), otherwise cfMesh if meshDict exists (multi-threaded). Then "
            "createPatch/changeDictionary if their dictionaries exist and checkMesh. A mesher "
            "entry stops after the mesh.",
            "controlDict: startFrom latestTime, stopAt endTime and runTimeModifiable true are "
            "set (needed for relaunch and soft stop).",
            "If 0.orig/ or 0.org/ exists it replaces 0/. Post-processing fields left in 0/ "
            "(yPlus, wallShearStress, wallHeatFlux, vorticity, Ma, total/static pressure...) "
            "are removed. setFields runs if setFieldsDict exists.",
            "Multi-region (regionProperties) and FSI cases are decomposed with all regions.",
            "If the machine is interrupted while writing, the incomplete last time step is "
            "deleted and the solver resumes.",
            "At the end all time steps are reconstructed and the processor* folders removed; "
            "case.foam is created for ParaView. dynamicCode is always rebuilt.",
        ],
        "options": [
            "Optional cloudHPC entries in system/controlDict (ignored by OpenFOAM):",
            "potentialFoam true; -> potentialFoam initialisation before the solver",
            "scaleFactor <s>; -> scales the generated mesh",
            "splitMesh true; (or largest;) -> splits cell zones into regions (or keeps the "
            "largest region)",
            "nExtrusion <n>; -> extrusions from system/extrudeMeshDict.0 ... .<n-1>",
            "intSurface true; -> internal faces/baffles from createBafflesDict.intFace and "
            ".bafFace",
            "NonConformalCouples true; -> non-conformal couples from createBafflesDict.nonConf "
            "(patches nonCouple1/nonCouple2)",
            "A system/fvSchemes-transient file: after the first (steady) run it replaces "
            "fvSchemes and the solver runs again (steady -> transient).",
        ],
        "restart": [
            "Relaunch in the same storage folder: the run continues from the latest time "
            "(startFrom latestTime is enforced).",
        ],
        "parallel": "Always parallel (MPI), one process per physical core: at least 2 physical "
                    "cores, on highcore or hypercore.",
        "resources": "At least 50,000 cells per core; highcore or hypercore only.",
        "logs": ["log.<application>", "log.decomposePar", "log.snappyHexMesh / log.blockMesh / "
                 "log.checkMesh", "Allrun.log (if Allrun is used)", "patchSummary.log"],
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
        "name": "CalculiX (2.18-PARDISO-MPI, 2.19-PARDISO, 2.21)",
        "input": [
            "The .inp input at the root of the case folder. With several .inp files only the "
            "first in alphabetical order is run: give *INCLUDE files another extension (e.g. "
            ".msh, .nam) or make sure the main file sorts first.",
        ],
        "automatic": ["Spaces in .inp file names are replaced with underscores."],
        "parallel": "calculiX-2.19-PARDISO and calculiX-2.21: one process, multi-threaded on "
                    "all vCPU. calculiX-2.18-PARDISO-MPI: one MPI process per physical core, "
                    "with the hyperthreads used as threads.",
        "resources": "PARDISO scales up to about 200,000 nodes per core; set SOLVER=PARDISO on "
                     "*STATIC. Start on highcpu, then standard, then highmem after a memory "
                     "error.",
        "logs": ["calculix.log"],
        "outputs": "CalculiX native outputs (.frd, .dat) in the case folder.",
        "docs": f"{DOCS}/scalability/#calculix",
    },
    "code_aster": {
        "name": "code_aster 17.0 (MPI)",
        "input": [
            "One .export file, the .comm file(s) and the mesh (.med/.unv), all at the root of "
            "the case folder. Paths written in the .export are reduced to the file name, so "
            "paths from your PC are fine as long as the files are in the folder.",
            "A .med file in an attachments/ sub-folder (Salome-Meca) is copied as mesh.med.",
        ],
        "automatic": [
            "Windows line endings in the .export are fixed.",
            "Run settings in the .export (memory limit, time limit, mode, work folder) are set "
            "automatically: no need to tune them.",
            "MPI processes = mpi_nbcpu of the .export, but only if the .comm contains "
            "NB_SOUS_DOMAINE or NIVEAU_PARALLELISME; otherwise one process.",
            "OpenMP threads per process (ncpus) = vCPU / processes / 2.",
            "Memory per process: about 60% of the machine RAM shared among the MPI processes "
            "(80% for a single process).",
        ],
        "parallel": "MPI + OpenMP. Select at least 2 x mpi_nbcpu vCPU.",
        "resources": "At least ~100,000 nodes per MPI process. Memory hungry: start on highcpu, "
                     "then standard, then highmem.",
        "logs": ["code_aster.log", "the .mess file"],
        "docs": f"{DOCS}/scalability/#code_aster",
    },
    "codesaturne": {
        "name": "code_saturne 9.0.1",
        "input": [
            "A code_saturne case: a folder with DATA/ and SRC/ (as created by the code_saturne "
            "GUI or 'code_saturne create'), at the root of the uploaded folder or one level "
            "down (the first sub-folder with both is used).",
            "The setup .xml in DATA/ is used automatically; user sources in SRC/ are compiled "
            "by code_saturne.",
        ],
        "automatic": [
            "About a minute after start, the residuals.csv of the run is linked at the storage "
            "folder root as <run>-residuals.csv, so convergence can be followed while running.",
        ],
        "parallel": "MPI, one process per physical core, 1 thread each: use highcore or "
                    "hypercore.",
        "resources": "Start with ~50,000 cells per core.",
        "logs": ["code_saturne.log", "RESU/<run>/ (listing, run_solver.log)"],
        "outputs": "Results in RESU/<run>/ inside the case.",
        "docs": "https://www.code-saturne.org/documentation/",
    },
    "su2": {
        "name": "SU2 (8.2.0, 8.3.0)",
        "input": [
            "One .cfg configuration file and the mesh it references, at the root of the case "
            "folder. With several .cfg only the first in alphabetical order is run.",
            "A file named config_CFD.cfg is deleted at start (it is written by the parallel "
            "run itself): do not use that name for your configuration.",
        ],
        "automatic": ["The mesh is partitioned, the solver run in parallel and the solution "
                      "merged automatically."],
        "parallel": "MPI, one process per physical core: use highcore or hypercore.",
        "resources": "Start with ~50,000 cells per core.",
        "logs": ["su2.log", "history.csv"],
        "docs": "https://su2code.github.io/docs_v7/",
    },
    "swan": {
        "name": "SWAN 41.51",
        "input": ["One .swn command file (with several, the first in alphabetical order) plus "
                  "the bathymetry, wind and boundary files it references, in the same folder."],
        "automatic": ["Spaces in .swn file names are replaced with underscores."],
        "parallel": "Multi-threaded (OpenMP) on all vCPU: hyperthreaded types are fine.",
        "resources": "Start with 8 vCPU on highcpu; move to standard/highmem after a memory "
                     "error.",
        "logs": ["swan.log", "PRINT file of the run"],
        "docs": "https://swanmodel.sourceforge.io/online_doc/online_doc.htm",
    },
    "xbeach": {
        "name": "XBeach (MPI)",
        "input": ["params.txt and the files it references (bathymetry, grids, wave boundary "
                  "conditions) at the root of the case folder."],
        "automatic": [],
        "parallel": "MPI, one process per physical core: use highcore or hypercore.",
        "resources": "Start with 8 vCPU; the domain is split along the grid, so very small "
                     "grids do not benefit from many cores.",
        "logs": ["xbeach.log", "XBlog.txt", "XBerror.txt"],
        "docs": "https://xbeach.readthedocs.io",
    },
    "custom": {
        "name": "Custom scripts (bash / Python, Ubuntu 24.04)",
        "input": [
            "Bash scripts (*.sh) and/or Python scripts (*.py) at the root of the case folder, "
            "with their data.",
            "For Python: a requirements.txt with the packages to install.",
        ],
        "automatic": [
            "All *.sh files run first, one after the other in alphabetical order; the output "
            "of each goes to <script>.sh.log.",
            "Then all *.py files run in alphabetical order inside a fresh Python virtual "
            "environment where requirements.txt is installed (install log in "
            "requirements.log); the output of each goes to <script>.py.log.",
        ],
        "parallel": "Whatever the scripts do: the machine's cores are available to them.",
        "resources": "Size the machine on what the scripts do. Never 1 vCPU on highcpu for "
                     "heavy work.",
        "logs": ["<script>.sh.log / <script>.py.log", "requirements.log"],
        "docs": DOCS,
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
            "One .idf model (with several, only the first in alphabetical order is run). "
            "epJSON models are not picked up: export or convert to .idf.",
            "Optional: one .epw weather file (without it only design days can run), a custom "
            ".idd, or a .imf macro file (EP-Macro and ExpandObjects are then run).",
        ],
        "before_upload": [
            "The Version object in the IDF must match the solver you launch (9.4.0, 9.6.0 or "
            "25.2.0): convert older models locally with IDFVersionUpdater. A version mismatch "
            "is the most common failure.",
            "HVACTemplate objects are expanded only when the model is uploaded as .imf: for a "
            "plain .idf, expand it locally (ExpandObjects, or export the expanded model from "
            "your tool) before uploading.",
            "External files (Schedule:File CSVs, FMUs, window data files) must be in the case "
            "folder and referenced with relative paths, no C:\\... paths. File names are "
            "case-sensitive (Schedules.csv is not schedules.csv).",
            "Test locally with design days first (energyplus -D -w weather.epw model.idf) and "
            "check the .err file for severe errors before paying for an annual run.",
            "One model per run: for a parametric study, create one case folder per variant and "
            "launch one run each.",
        ],
        "automatic": [
            "Spaces in .idf, .epw, .idd and .imf file names are replaced with underscores.",
            "Versions 9.6.0 and 25.2.0 run multi-threaded on all vCPU; 9.4.0 runs on one thread.",
        ],
        "parallel": "Multi-threaded (9.6.0 and later); a single simulation rarely benefits from "
                    "more than a few threads.",
        "resources": "2-4 vCPU on highcpu are enough for most buildings.",
        "logs": ["energyplus.log", "eplusout.err (EnergyPlus errors and warnings)"],
        "outputs": "EnergyPlus native outputs (.eso, .sql, .htm tables, .err). Time-series CSV "
                   "files are not generated automatically: read the .sql/.eso or convert them "
                   "locally with ReadVarsESO.",
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
    "codesaturne": "codesaturne",
    "code_saturne": "codesaturne",
    "swan": "swan",
    "xbeach": "xbeach",
    "custom-script": "custom",
}


# vCPU vs physical cores: the most common source of "wrong number of processes"
CORES_RULE = (
    "Physical cores = vCPU on highcore/hypercore, but vCPU / 2 on the hyperthreaded types "
    "(highcpu, standard, highmem, hypercpu). This solver runs one MPI process per PHYSICAL "
    "core, so on a hyperthreaded type it uses half the vCPU selected. Example: 32 vCPU on "
    "standard = 16 physical cores = 16 MPI processes (numberOfSubdomains is set to 16); 32 vCPU "
    "on highcore or hypercore = 32 processes. To run N processes select N vCPU on highcore or "
    "hypercore."
)
PHYSICAL_CORE_FAMILIES = {"openfoam", "dafoam", "su2", "codesaturne", "xbeach", "telemac",
                          "liggghts"}

FAQ: dict[str, list[dict[str, str]]] = {
    "openfoam": [
        {"q": "I selected 32 vCPU and set numberOfSubdomains 32, but the run used 16 "
              "subdomains / my decomposeParDict was changed.",
         "a": "Expected on a hyperthreaded RAM type (highcpu, standard, highmem, hypercpu): "
              "32 vCPU there are 16 physical cores and OpenFOAM uses physical cores only, so "
              "cloudHPC sets numberOfSubdomains to 16 (methods scotch and hierarchical are "
              "adapted automatically). To run 32 subdomains select 32 vCPU on highcore or "
              "hypercore; the dictionary is then set to 32. Nothing else to change."},
        {"q": "Can I keep my own decomposition?",
         "a": "With methods other than scotch or hierarchical the dictionary is left as it is; "
              "numberOfSubdomains must then be at most the physical cores selected, otherwise "
              "the run stops."},
    ],
}


def guide_for(family: str) -> dict[str, Any] | None:
    g = GUIDES.get(family)
    if g is None:
        return None
    g = dict(g)
    if family in PHYSICAL_CORE_FAMILIES:
        g["cores"] = CORES_RULE
    if family in FAQ:
        g["faq"] = FAQ[family]
    return g

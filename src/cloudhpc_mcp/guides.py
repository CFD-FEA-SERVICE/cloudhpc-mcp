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
            "PyroSim: use the file written by File > Export > FDS File and check MPI_PROCESS: "
            "it decides how many MPI processes run.",
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
            "runs, especially on preemptible instances (they can be restarted by the provider, "
            "typically up to about once a day). Aim for a restart file every few hours of run "
            "time: very short intervals slow the run and fill the disk.",
            "After an automatic restart the log shows the restart and FDS continues from the "
            "last restart file: nothing to do. 'NO RESTART FILE => NOTHING TO DO' at start is a "
            "normal check, not an error.",
            "Without .restart files the run starts from the beginning and previous outputs "
            "are removed.",
            "Relaunch with the same vCPU and RAM type: a different mesh split discards the "
            "restart files.",
            "FDS+EVAC cases cannot be restarted.",
        ],
        "monitoring": [
            "Progress = simulated time / T_END: the output of the run shows the FDS time steps "
            "and simulated time.",
            "While the run is RUNNING, Smokeview on the remote desktop shows the results live "
            "(activated accounts).",
            "If a run seems slow with RAM to spare, the cause is usually the time step: fine "
            "cells mean small time steps (see 'Controlling the Time Step' in the FDS User "
            "Guide).",
        ],
        "outputs": "FDS.tar.gz in the case folder contains all FDS outputs. Extract it with "
                   "7-Zip or similar (on Windows twice: .gz then .tar). For PyroSim's results "
                   "viewer, extract it in the PyroSim results folder of the model. The size "
                   "depends mostly on the outputs requested (SLCF, BNDF, DT_ intervals), not "
                   "only on the mesh.",
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
    "desktop": {
        "name": "Remote desktop (static instance): ubuntu-static, windows-static",
        "input": ["A storage folder to work on (e.g. the folder of a finished simulation)."],
        "automatic": [
            "A machine with a graphical desktop starts on the selected vCPU and RAM type; "
            "open it in the browser with the remote-desktop link once it is RUNNING.",
            "Use it for post-processing (ParaView, Smokeview, ...) without downloading the "
            "results.",
        ],
        "parallel": "Interactive use: choose vCPU and RAM for the size of the results to open.",
        "resources": "Large results need RAM: prefer standard or highmem over highcpu. "
                     "windows-static adds a Windows licence cost.",
        "logs": [],
        "important": "The desktop does not stop by itself: it is billed per vCPU-hour until "
                     "you stop it. Only on activated accounts.",
        "docs": "https://docs.cloudhpc.cloud/simulation/#execute_a_static_instance",
    },
    "postprocess": {
        "name": "cloudHPC-postProcess (automatic post-processing)",
        "input": ["The storage folder of a finished simulation."],
        "automatic": ["Generates post-processing images automatically from the results and "
                      "saves them in POSTPROCESS.tar.gz in the same folder."],
        "parallel": "Not a solver run: a few vCPU are enough.",
        "resources": "Start with a small machine; use more RAM for very large results.",
        "logs": [],
        "outputs": "POSTPROCESS.tar.gz in the case folder.",
        "docs": DOCS,
    },    "precice": {
        "name": "preCICE 3.3.1 (coupled FSI / CHT simulations with OpenFOAM, CalculiX and code_aster participants)",
        "input": [
            "precice-config.xml at the root of the case folder.",
            "One subfolder per participant, as in the preCICE tutorials. OpenFOAM participant: "
            "a folder with system/controlDict and system/preciceDict containing "
            "'participant <name>;'. CalculiX participant: a folder with config.yml (the "
            "participant name under 'participants:') and an .inp file containing *STEP. "
            "code_aster participant (FSI or CHT): a folder with an .export (with an 'F comm' "
            "line) and a .comm that uses the precice_aster adapter; give the participant name "
            "with participant=\"<name>\" in the run_fsi / run_cht call of the .comm (it can be "
            "omitted only when code_aster is the one participant left without a folder).",
            "Each participant of precice-config.xml needs exactly ONE folder. The tutorials "
            "often ship alternatives for the same participant (e.g. solid-calculix and "
            "solid-openfoam): keep only the one to run, otherwise the run stops. Folders whose "
            "participant is not in precice-config.xml are ignored.",
            "Supported participants: OpenFOAM, CalculiX and code_aster; folders that contain "
            "only a run.sh (other solvers) are ignored.",
            "Paths in precice-config.xml and in the adapters (e.g. exchange-directory) should "
            "be relative, as in the tutorials.",
        ],
        "automatic": [
            "The tutorials' run.sh / clean.sh scripts are NOT executed: each participant's "
            "solver is started directly from its folder, so put every setting in the case "
            "files, not in those scripts.",
            "All participants start together; if one fails, the others are stopped.",
            "OpenFOAM participants: the newest installed OpenFOAM with the preCICE adapter is "
            "used; 0.orig is copied to 0 if 0 is missing; the mesh is generated with "
            "blockMesh and/or snappyHexMesh if constant/polyMesh is missing; setFields runs if "
            "system/setFieldsDict exists. With more than one core the case is decomposed "
            "(numberOfSubdomains set automatically; methods other than scotch, ptscotch, metis "
            "or kahip are replaced by scotch; decomposeParDict is created if missing), run in "
            "parallel and reconstructed at the end.",
            "CalculiX participants: the first .inp file containing *STEP is run with the "
            "participant name.",
            "code_aster participants: the .export with the 'F comm' line is run with one MPI "
            "process (the adapter is serial) and its thread count (P ncpus) is set "
            "automatically; P mpi_nbcpu is set to 1. code_aster runs in a temporary folder, "
            "so when it takes part the exchange-directory in precice-config.xml is made "
            "absolute automatically (the file in the case folder is updated).",
            "Exchange folders (precice-run) left by a previous run are removed before start.",
        ],
        "parallel": "The physical cores are split among the participants: each CalculiX or "
                    "code_aster participant gets an equal share as threads, at most 8 when "
                    "OpenFOAM participants are present (they scale little beyond); the remaining "
                    "cores are split equally among the OpenFOAM participants (one MPI process "
                    "per core). The split is printed at the start of the log.",
        "resources": "Use highcore or hypercore (physical cores). Tutorials run fine on 4-8 "
                     "vCPU; size larger cases on the OpenFOAM mesh (~50,000-100,000 cells per "
                     "core) plus up to 8 cores per CalculiX or code_aster participant.",
        "restart": [
            "Coupled runs cannot be resumed (preCICE writes no checkpoint): relaunching an "
            "incomplete run starts it from the beginning. A completed run is not run again "
            "in the same folder.",
        ],
        "logs": ["log.<participant> in each participant folder",
                 "OpenFOAM steps: log.blockMesh, log.snappyHexMesh, log.decomposePar, "
                 "log.reconstructPar", "simulation output lines are prefixed with [participant]"],
        "docs": "https://precice.org/docs.html",
    },
    "lammps": {
        "name": "LAMMPS (molecular dynamics)",
        "input": [
            "LAMMPS input files at the root of the case folder, named in.*, *.in, *.lmp or "
            "*.lammps, with every file they read (read_data, include, potentials).",
            "Main inputs are the files with a run, minimize or rerun command. Files without "
            "them (data files, settings) and files loaded by another input with 'include' are "
            "not run on their own.",
            "Several main inputs run one after the other, each with the whole machine.",
            "Command-line variables (-var) cannot be passed: define every variable inside the "
            "input with the 'variable' command. The input cannot wait for keyboard input.",
        ],
        "automatic": [
            "MPI build: one MPI process per physical core, one thread each.",
            "On GPU instances, if the installed LAMMPS has the GPU package, GPU acceleration "
            "(gpu suffix, all GPUs) is switched on automatically; otherwise it runs on CPU and "
            "a warning is printed.",
            "The screen output is saved in log.lammps (log.<input>.lammps with several "
            "inputs); LAMMPS' own log file is not written, so 'log' commands in the input are "
            "not needed.",
        ],
        "parallel": "One MPI process per physical core: use highcore or hypercore.",
        "resources": "Keep at least ~1,000-2,000 atoms per core: below that, extra cores do "
                     "not speed up the run. 8 vCPU highcore is a good start for 10^4-10^5 "
                     "atoms; use a GPU instance only for large systems with GPU-enabled pair "
                     "styles.",
        "restart": [
            "LAMMPS continues a run only from a restart file with an input that starts with "
            "read_restart. Write restart files during long runs (restart command) and, to "
            "continue, add a new input that reads them. Relaunching the folder skips the "
            "inputs already completed and runs the others from the start.",
        ],
        "logs": ["log.lammps (or log.<input>.lammps)"],
        "docs": "https://docs.lammps.org",
    },
    "palabos": {
        "name": "Palabos 2.3.0 (lattice Boltzmann, C++ library)",
        "input": [
            "Your C++ program: one or more .cpp files with main(), in the case folder or in "
            "its direct subfolders (one case per main()). Other .cpp files without main() in "
            "the same folder are compiled together with it; headers next to them are found.",
            "Your CMakeLists.txt or Makefile is NOT used: cloudHPC compiles each program with "
            "Palabos 2.3.0 (C++17, optimised, MPI). Code that builds with the standard "
            "Palabos examples' CMake setup builds here too.",
            "Parameter file: if the program reads argv(1) and its folder contains exactly one "
            ".xml file, that file is passed as the argument (like param.xml in the "
            "showCases). Several .xml files stop the run: keep one. Only this one argument "
            "is passed.",
            "Geometry archives (.tgz, .tar.gz, e.g. STL files) are extracted automatically if "
            "their content is missing.",
        ],
        "automatic": [
            "Output folders written in setOutputDir(\"...\") (relative paths) are created "
            "before the run.",
            "Cases run one after the other, each with all physical cores (MPI).",
        ],
        "parallel": "One MPI process per physical core: use highcore or hypercore.",
        "resources": "8 vCPU highcore as a start; memory grows with the lattice size, so "
                     "large 3D lattices may need more vCPU for the RAM.",
        "restart": [
            "Checkpointing is part of your program (Palabos save/load functions). Relaunching "
            "the folder skips the cases already completed; the others start again.",
        ],
        "logs": ["log.compile (compilation errors are here)", "log.<program name>"],
        "docs": "https://palabos.unige.ch",
    },
    "freefem": {
        "name": "FreeFEM 4.15 (finite elements, .edp scripts)",
        "input": [
            ".edp scripts at the root of the case folder. Scripts loaded by another .edp with "
            "include are not run on their own (include files are usually .idp).",
            "Graphics are off: plot() windows are not shown, so save results to files "
            "(e.g. savevtk, savemesh, ofstream).",
            "No keyboard input: scripts must not read from cin.",
        ],
        "automatic": [
            "MPI scripts are recognised from mpirank/mpisize, processor(), broadcast(), "
            "buildDmesh, createMat, macro dimension, or load \"PETSc\", \"SLEPc\", \"hpddm\", "
            "\"MUMPS_mpi\", \"parmetis\", \"ptscotch\" (also in the included files). To force "
            "MPI, add a comment containing the words \"run with MPI\" to the script.",
            "MPI scripts run one at a time with one process per physical core.",
            "Sequential scripts run in parallel with each other; the physical cores are "
            "shared among them as threads for multi-threaded solvers.",
        ],
        "parallel": "MPI scripts: one process per physical core (highcore or hypercore). "
                    "A single sequential script uses few cores: 2-4 vCPU are enough.",
        "resources": "Sequential scripts: 2-4 vCPU (several scripts run side by side on "
                     "more). MPI scripts (PETSc, hpddm): 8+ vCPU on highcore or hypercore.",
        "restart": [
            "FreeFEM has no generic restart: relaunching the folder skips the scripts already "
            "completed and runs the others from the start.",
        ],
        "logs": ["log.<script name>"],
        "docs": "https://doc.freefem.org",
    },
    "tflows": {
        "name": "T-Flows 1.1 (unstructured finite-volume CFD)",
        "input": [
            "A 'control' file in the case folder (or in each subfolder, run one after the "
            "other), with PROBLEM_NAME.",
            "Grid, one of: <PROBLEM_NAME>.cfn and .dim already made; generate*.scr scripts "
            "with their .dom file (run with Generate); convert*.scr scripts with the mesh "
            "file (Gmsh 4.1 .msh, Fluent, ...; run with Convert). A .geo without .msh is "
            "meshed with Gmsh (format 4.1) when available.",
            "Grid archives (.tgz, .tar.gz, .gz) are extracted if their content is missing.",
            "User_Mod folders are NOT compiled: the standard Process is used (a warning is "
            "printed). Cases that need user functions cannot run as they are.",
        ],
        "automatic": [
            "divide*.scr is ignored: the grid is divided into one part per physical core.",
            "Process runs with MPI when the installed build supports it, otherwise with "
            "OpenMP threads on the physical cores (shown at the start of the log).",
        ],
        "parallel": "One process (or thread) per physical core: use highcore or hypercore.",
        "resources": "Start from ~50,000 cells per core; 8 vCPU highcore for small cases.",
        "restart": [
            "Set BACKUP_SAVE_INTERVAL in control for long runs, especially on preemptible "
            "instances. Relaunch in the SAME storage folder: if <PROBLEM_NAME>-tsNNNNNN.backup "
            "files exist, the run restarts from the latest one automatically (your control "
            "file is restored at the end). Without backups it starts from scratch.",
        ],
        "logs": ["log.Generate / log.Convert / log.gmsh", "log.Divide", "log.Process"],
        "docs": "https://github.com/DelNov/T-Flows",
    },
    "dualsphysics": {
        "name": "DualSPHysics 5.4 (SPH)",
        "input": [
            "One or more <name>_Def.xml case definitions: each runs GenCase, the solver and "
            "the post-processing. The official examples work as they are (their _Def.xml).",
            "Alternatively a case already generated by GenCase: <name>.xml + <name>.bi4 in "
            "the folder or in one subfolder (solver and post-processing only).",
            "The examples' xCase*.sh scripts are NOT executed: options set only there are not "
            "applied. Put the solver parameters in <execution><parameters> of the _Def.xml.",
        ],
        "automatic": [
            "Output in <name>_out/: GenCase files, data/ (Part_*.bi4), particles/ with VTK "
            "files of fluid (PartFluid) and boundaries (PartBound), and floatinginfo/ with "
            "the motion of floating bodies when the case has <floating>.",
            "GPU instances: one case per GPU, several cases in parallel (GPUs beyond the "
            "number of cases stay idle). CPU instances: cases one after the other, each "
            "using all vCPU.",
        ],
        "parallel": "GPU: one case per GPU. CPU: OpenMP threads on all vCPU (hyperthreaded "
                    "types are fine).",
        "resources": "Use a GPU instance (e.g. basegpu): DualSPHysics is many times faster "
                     "on GPU. CPU only for small tests (highcpu, all vCPU).",
        "restart": [
            "Relaunch in the SAME storage folder: if Part_*.bi4 files exist the run restarts "
            "from the last PART into a new data_rNNNN folder (previous data are kept). Cases "
            "already completed are not recomputed.",
        ],
        "logs": ["<name>_out/log.GenCase", "<name>_out/log.DualSPHysics",
                 "<name>_out/log.PartVTK, log.FloatingInfo"],
        "docs": "https://dual.sphysics.org",
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
    "ubuntu-static": "desktop",
    "windows-static": "desktop",
    "cloudhpc-postprocess": "postprocess",
    "precice": "precice",
    "lammps": "lammps",
    "palabos": "palabos",
    "freefem": "freefem",
    "t-flows": "tflows",
    "tflows": "tflows",
    "dualsphysics": "dualsphysics",
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
                          "liggghts", "precice", "lammps", "palabos", "freefem", "tflows"}

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
    from .live import describe
    live_info = describe(family)
    if live_info:
        g["live_update"] = dict(live_info, how="While RUNNING: live_settings, then "
                                               "update_running_simulation.")
    return g

"""Live updates of RUNNING simulations through the "_push" storage folder.

Files written to <case folder>/_push/<relative path> in the storage are moved
by the simulation machine into the case folder at the sync that
update_running_simulation requests; the simulation
output then shows a "push applied" line. Which files are accepted is checked here.

Only solvers that re-read a file while running are supported: anything else
would be copied but ignored until the next run.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

PUSH_DIR = "_push"
MAX_FILES = 5
MAX_BYTES = 256 * 1024

RULES: dict[str, dict[str, Any]] = {
    "openfoam": {
        "allowed": r"^system/(controlDict|fvSolution|fvSchemes)$",
        "files": ["system/controlDict", "system/fvSolution", "system/fvSchemes"],
        "what": "OpenFOAM re-reads these dictionaries while running (runTimeModifiable): "
                "endTime, writeInterval, deltaT/maxCo, stopAt writeNow, function objects, "
                "relaxation factors, solver tolerances, schemes. Mesh, 0/ and constant/ "
                "are read only at start.",
        "applied_when": "at the next time step after the copy; the solver log shows a "
                        "'Re-reading object' line",
    },
    "fds": {
        "allowed": r"^[^/]+\.stop$",
        "files": ["<CHID>.stop"],
        "what": "FDS only checks <CHID>.stop: an empty file stops the run cleanly, writing "
                "restart files if DT_RESTART is set (resume later with resume_simulation). "
                "Recent FDS versions read a time (s) in the file and stop at that time. "
                "Other parameters cannot be changed while FDS runs.",
        "applied_when": "at the next time step after the copy",
    },
    "codesaturne": {
        "allowed": r"^RESU/[^/]+/control_file$",
        "files": ["RESU/<run>/control_file"],
        "what": "code_saturne reads control_file in the run folder, e.g. "
                "'max_time_step 2000', 'time_step_limit', 'checkpoint_time_step', "
                "'postprocess_time_step', 'flush'. It deletes the file after reading it.",
        "applied_when": "at the next time step after the copy",
    },
    "openradioss": {
        "allowed": r"^[^/]+_[0-9]{4}\.ctl$",
        "files": ["<root>_0001.ctl"],
        "what": "The OpenRadioss / OpenCourant Engine reads <root>_<run>.ctl: one command per line, e.g. "
                "/STOP (stop writing restart), /CHKPT (restart now), /ANIM or /H3D (output "
                "now), /KILL (stop without restart). When the case has several engine files "
                "(_0001, _0002, ...), a .ctl ends the sequence: the following engine files "
                "are not run.",
        "applied_when": "at the next cycle after the copy",
    },
}

SUPPORTED = sorted(RULES)


def rule_for(family: str) -> dict[str, Any] | None:
    return RULES.get(family)


def describe(family: str) -> dict[str, Any] | None:
    r = RULES.get(family)
    if r is None:
        return None
    return {"files": r["files"], "what": r["what"], "applied": r["applied_when"]}


def clean_path(path: str) -> str | None:
    """Normalised relative path, or None if it could escape the case folder."""
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or any(part in ("", ".", "..") for part in p.split("/")):
        return None
    return p


def is_allowed(family: str, path: str) -> bool:
    r = RULES.get(family)
    return bool(r and re.match(r["allowed"], path))


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _end_time(text: str) -> float | None:
    m = re.search(rf"^\s*endTime\s+({_NUM})\s*;", text, re.M)
    return float(m.group(1)) if m else None


def check(family: str, path: str, text: str, current: str | None,
          folder_files: set[str]) -> tuple[list[str], list[str]]:
    """Validate one file. Returns (errors, notes)."""
    errs: list[str] = []
    notes: list[str] = []
    if "\x00" in text:
        errs.append(f"{path}: binary content is not accepted.")
        return errs, notes
    if len(text.encode()) > MAX_BYTES:
        errs.append(f"{path}: larger than {MAX_BYTES // 1024} KB.")
        return errs, notes
    name = path.rsplit("/", 1)[-1]

    if family == "openfoam":
        if "FoamFile" not in text:
            errs.append(f"{path}: not an OpenFOAM dictionary (no FoamFile header). Send "
                        "the whole file, not only the changed lines.")
        if text.count("{") != text.count("}"):
            errs.append(f"{path}: unbalanced braces {{ }}.")
        if name == "controlDict":
            if re.search(r"^\s*runTimeModifiable\s+(false|no|off|0)\s*;", text, re.M):
                errs.append("controlDict: runTimeModifiable is off, so OpenFOAM would ignore "
                            "every live change.")
            new_end, old_end = _end_time(text), _end_time(current or "")
            if new_end is not None and old_end is not None and new_end > old_end:
                notes.append(f"endTime goes from {old_end:g} to {new_end:g}: the run lasts "
                             "longer and costs more.")
            if re.search(r"^\s*stopAt\s+writeNow\s*;", text, re.M):
                notes.append("stopAt writeNow: OpenFOAM writes the current time and stops.")
            if current and re.search(r"^\s*startFrom\s+latestTime\s*;", current, re.M) and \
                    not re.search(r"^\s*startFrom\s+latestTime\s*;", text, re.M):
                notes.append("startFrom latestTime is missing: cloudHPC sets it for resumes; "
                             "keep it as in the current file.")
    elif family == "fds":
        chid = name[:-len(".stop")]
        if not ({f"{chid}.fds", f"{chid}.out"} & folder_files):
            chids = sorted({f.rsplit(".", 1)[0] for f in folder_files if f.endswith(".fds")})
            errs.append(f"{name}: no FDS case with CHID '{chid}' in the folder"
                        + (f" (CHIDs found: {', '.join(chids)})." if chids else "."))
        body = text.strip()
        if body and not re.fullmatch(_NUM, body):
            errs.append(f"{name}: must be empty (stop now) or contain only a time in seconds.")
        notes.append("FDS stops" + (f" at t = {body} s" if body else " at the next time step")
                     + "; restart files are written only if DT_RESTART is set.")
    elif family == "openradioss":
        if f"{name[:-len('.ctl')]}.rad" not in folder_files:
            rads = sorted(f for f in folder_files if re.search(r"_[0-9]{4}\.rad$", f))
            errs.append(f"{name}: no Engine file {name[:-4]}.rad in the folder"
                        + (f" (found: {', '.join(rads)})." if rads else "."))
        bad = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("/")]
        if bad:
            errs.append(f"{name}: every line must be an Engine command starting with '/' "
                        f"(e.g. /STOP, /CHKPT, /ANIM); got '{bad[0].strip()}'.")
        if re.search(r"^\s*/KILL", text, re.M):
            notes.append("/KILL stops the run without writing a restart file.")
        stem = name[:-len(".ctl")]
        later = sorted(f for f in folder_files if re.fullmatch(r".+_[0-9]{4}\.rad", f)
                       and not f.endswith("_0000.rad") and f > f"{stem}.rad"
                       and f.rsplit("_", 1)[0] == stem.rsplit("_", 1)[0])
        if later:
            notes.append(f"After {stem}.rad ends, the next engine files ({', '.join(later)}) "
                         "will NOT run because a .ctl file is present.")
    elif family == "codesaturne":
        if not text.strip():
            errs.append("control_file is empty.")
    return errs, notes


def diff(path: str, current: str | None, new: str, max_lines: int = 40) -> str | None:
    if current is None:
        return None
    lines = list(difflib.unified_diff(current.splitlines(), new.splitlines(),
                                      f"current/{path}", f"new/{path}", lineterm="", n=1))
    if not lines:
        return "(no change)"
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... {len(lines) - max_lines} more lines"]
    return "\n".join(lines)

# PyInstaller spec for the job-applier backend sidecar (Phase 6).
#
# The hazard is dynamic imports: source adapters are imported by name in
# sources/__init__.py, so static analysis misses them. collect_submodules pulls
# the whole job_applier package (every adapter) plus uvicorn's dynamic workers.
# The ai/prompts/*.md templates are read via importlib.resources, so they ship
# as data. Playwright is excluded — the packaged app prints via Electron.
#
# Build:  uv run pyinstaller desktop/sidecar/job-applier-backend.spec
# Output: dist/job-applier-backend/  (onedir)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("job_applier")
    + collect_submodules("uvicorn")
    + [
        "anyio",
        "sqlalchemy.dialects.sqlite",
    ]
)

datas = collect_data_files("job_applier", includes=["ai/prompts/*.md"])

# SPECPATH is the directory containing this spec, so `make sidecar` works from the
# repo root regardless of the current working directory.
import os  # noqa: E402

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["playwright", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="job-applier-backend",
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="job-applier-backend",
)

# PyInstaller recipe for the standalone Nervus application: pyinstaller packaging/nervus.spec
# (run from the repository root; the result is in dist/: Nervus.app on macOS, a Nervus folder elsewhere).
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH is set by PyInstaller)
datas = collect_data_files("nervus", include_py_files=True)  # help pages and figures
# The examples, the contributors and the licence are next to the package in a source checkout (the wheel puts them
# inside it): take them from the repository, without caches and generated FMUs.
for f in sorted((ROOT / "examples").rglob("*")):
    if f.is_file() and "__pycache__" not in f.parts and f.suffix not in (".pyc", ".fmu"):
        datas.append((str(f), str(Path("nervus/examples") / f.parent.relative_to(ROOT / "examples"))))
datas += [(str(ROOT / "CONTRIBUTORS.md"), "nervus"), (str(ROOT / "LICENSE"), "nervus")]
hidden = collect_submodules("nervus")  # blocks are registered when their modules are imported
# Parts of Qt that Nervus does not use (they would make the application hundreds of MB larger).
UNUSED_QT = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtQuick",
             "QtQuick3D", "QtQml", "QtQuickWidgets", "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic",
             "Qt3DAnimation", "Qt3DExtras", "QtMultimedia", "QtMultimediaWidgets", "QtCharts", "QtDataVisualization",
             "QtGraphs", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors", "QtSerialBus",
             "QtRemoteObjects", "QtScxml", "QtSql", "QtTextToSpeech", "QtPdf", "QtPdfWidgets", "QtSpatialAudio",
             "QtHttpServer", "QtDesigner", "QtUiTools", "QtHelp"]

a = Analysis(
    ["nervus_app.py"],
    pathex=[],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "IPython", "pytest", *[f"PySide6.{m}" for m in UNUSED_QT]],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Nervus", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Nervus")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Nervus.app",
        bundle_identifier="io.github.ikjbi.nervus",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": __import__("nervus").__version__},
    )

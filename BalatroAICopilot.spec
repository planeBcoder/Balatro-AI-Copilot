from pathlib import Path
import os

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'prompts'), 'prompts'), (str(root / 'assets'), 'assets'), (str(root / 'dependencies'), 'dependencies')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['openai', 'pytest', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'],
    noarchive=False,
    optimize=0,
)
# Qt 6.11's Windows wheel imports unsuffixed ICU APIs provided by Windows 10+
# System32/icuuc.dll. The bundled developer runtime has a DIFFERENT ICU 78 DLL
# with suffixed exports; PyInstaller's PATH scan must not shadow the system DLL.
a.binaries = [item for item in a.binaries if Path(item[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
# Only Widgets is used. Drop auto-collected QML/input/PDF plugins and their
# unused transitive libraries (including GPL-only VirtualKeyboard).
qt_dlls={'qt6core.dll','qt6gui.dll','qt6widgets.dll','qt6svg.dll','qt6network.dll','qt6opengl.dll'}
qt_plugins={'qwindows.dll','qmodernwindowsstyle.dll','qjpeg.dll','qgif.dll','qico.dll','qsvg.dll','qwebp.dll'}
def shipped_binary(item):
    dest=item[0].replace('\\','/');name=Path(dest).name.lower()
    if name.startswith('qt6') and name.endswith('.dll'):return name in qt_dlls
    if dest.lower().startswith('pyside6/plugins/'):return name in qt_plugins
    return True
a.binaries=[item for item in a.binaries if shipped_binary(item)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='BalatroAICopilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get('COPILOT_BUILD_CONSOLE') == '1',
    contents_directory='_internal',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BalatroAICopilot')

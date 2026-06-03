# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for PaperMatcher macOS app bundle.

Build with: pyinstaller PaperMatcher.spec
Output: dist/PaperMatcher.app
"""

import sys as _sys
_sys.path.insert(0, ".")
from app.version import __version__ as APP_VERSION

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("app", "app"),
    ],
    hiddenimports=[
        "customtkinter",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaperMatcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PaperMatcher",
)

app = BUNDLE(
    coll,
    name="PaperMatcher.app",
    icon=None,
    bundle_identifier="com.papermatcher.app",
    info_plist={
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": "True",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "NSHumanReadableCopyright": "Copyright © 2026. MIT License.",
    },
)

import platform, subprocess
if platform.system() == "Darwin":
    subprocess.run(
        ["xattr", "-r", "-d", "com.apple.quarantine", "dist/PaperMatcher.app"],
        check=False,
    )
    # Create versioned DMG
    import os, shutil
    tmp = "dist/_dmg_tmp"
    dmg_path = f"dist/PaperMatcher_v{APP_VERSION}.dmg"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    shutil.copytree("dist/PaperMatcher.app", f"{tmp}/PaperMatcher.app")
    subprocess.run(
        ["hdiutil", "create", "-volname", f"PaperMatcher {APP_VERSION}",
         "-srcfolder", tmp, "-ov", "-format", "UDZO", dmg_path],
        check=True,
    )
    shutil.rmtree(tmp)

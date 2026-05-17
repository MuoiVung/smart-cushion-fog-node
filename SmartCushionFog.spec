# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(os.getcwd())

# Locate customtkinter dynamically
try:
    import customtkinter
    customtkinter_path = os.path.dirname(customtkinter.__file__)
    print(f"✅ Found CustomTkinter at: {customtkinter_path}")
except ImportError:
    customtkinter_path = None
    print("⚠️ Warning: CustomTkinter not found in the build environment!")

block_cipher = None

# Bundle customtkinter assets
datas = []
if customtkinter_path:
    # CustomTkinter needs its asset folders (themes, assets) bundled
    datas.append((customtkinter_path, 'customtkinter'))

# Bundle application resource directories and files
datas.append(('app.py', '.'))
datas.append(('ai/models/*', 'ai/models'))
datas.append(('core/*.py', 'core'))
datas.append(('utils/*.py', 'utils'))
datas.append(('config/*.py', 'config'))
datas.append(('data/schema.py', 'data'))

# Bundle certs if present
if os.path.exists('certs'):
    datas.append(('certs/*', 'certs'))

a = Analysis(
    ['run_launcher.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'websockets',
        'paho.mqtt.client',
        'sqlite3',
        'joblib',
        'sklearn',
        'sklearn.ensemble',
        'sklearn.preprocessing',
        'sklearn.utils.validation',
        'pandas',
        'numpy',
        'tensorflow',
        'scipy',
        'scipy.stats',
        'openpyxl',
        'pydantic',
        'pydantic_settings'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='SmartCushionFog',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Set to False to hide terminal window on app run
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
    name='SmartCushionFog',
)

app = BUNDLE(
    coll,
    name='SmartCushionFog.app',
    icon=None,
    bundle_identifier='com.smartcushion.fog',
)

# -*- mode: python ; coding: utf-8 -*-

import galacteek.database.models.pubchattokens
import galacteek.database.models.seeds
import galacteek.database.models.pubsub
import galacteek.database.models.atomfeeds
import galacteek.database.models.core
import tortoise.models
import inspect


block_cipher = None


source_mods = [
    tortoise.models,
    galacteek.database.models.core,
    galacteek.database.models.atomfeeds,
    galacteek.database.models.pubsub,
    galacteek.database.models.seeds,
    galacteek.database.models.pubchattokens
]


def collect_source_files(modules):
    datas = []
    for module in modules:
        print('Collecting source for', module)
        source = inspect.getsourcefile(module)
        dest = f"src.{module.__name__}"  # use "src." prefix
        datas.append((source, dest))
    return datas


source_files = collect_source_files(source_mods)
source_files_toc = TOC((name, path, 'DATA') for path, name in source_files)


a = Analysis(
    ['galacteek.py'],
    pathex=[
        "C:/Python37/Lib/site-packages/PyQt5/Qt/bin",
        "C:/Python37/Lib/site-packages/PyQt5/Qt",
        "C:/Python37/Lib/site-packages",
        "C:/Python37/Lib",
        "C:/Python37"
    ],
    binaries=[
        ('./packaging/windows/libmagic/libmagic-1.dll',
         '.'),
        ('./packaging/windows/libmagic/libgnurx-0.dll',
         '.'),
        ('./packaging/windows/zbar/libzbar-64.dll',
         '.')
    ],
    datas=[],
    hiddenimports=[
        'PyQt5',
        'galacteek',
        'tortoise',
        'tortoise.fields',
        'tortoise.backends',
        'tortoise.backends.sqlite',
        'tortoise.backends.base',
        'aioipfs'
    ],
    hookspath=['packaging/windows/hooks'],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)


pyz = PYZ(a.pure, a.zipped_data,
          source_files_toc,
          cipher=block_cipher)


exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='galacteek',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=True
          )
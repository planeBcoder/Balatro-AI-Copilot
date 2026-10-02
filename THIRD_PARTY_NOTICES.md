# Third-party components

No Balatro game files, artwork, music or saves are distributed. This unofficial
project is not endorsed by LocalThunk, Playstack, Steam, DeepSeek or Qt.

- Lovely 0.10.0: MIT, Ethan Green. See `licenses/Lovely-MIT.txt`.
  https://github.com/ethangreen-dev/lovely-injector/tree/v0.10.0
- Steamodded 26.829.0: GPL v3. Complete unmodified source and LICENSE are bundled
  in `_internal/dependencies/steamodded-26.829.0.zip`.
  https://github.com/Steamodded/smods/tree/26.829.0
- PySide6 / Shiboken / Qt 6.11.2: shipped components used under LGPL v3.
  License texts and upstream notices are in `licenses/`. Matching unmodified
  Qt Base, Qt SVG and PySide/Shiboken sources accompany the release in
  `Balatro-AI-Copilot-ThirdPartySources.zip`, with upstream build instructions.
  Qt DLLs in `_internal/PySide6` are dynamically linked; compatible replacements
  and reverse engineering for debugging modifications to these libraries are
  permitted. Upstream commercial-license notices do not imply commercial rights.
  https://code.qt.io/cgit/qt/qtbase.git/
  https://code.qt.io/cgit/qt/qtsvg.git/
  https://code.qt.io/cgit/pyside/pyside-setup.git/
- Python 3.12: PSF license in `licenses/Python-LICENSE.txt`.
- Pydantic / pydantic-core: MIT, wheel license texts in `licenses/`.
- PyInstaller bootloader: GPL with its distribution exception; upstream COPYING
  in `licenses/`.

Source, component copyright notices and third-party notices remain intact in
the accompanying archives. Rebuild Copilot from this repository using
`scripts/build-public.ps1`; the project MIT license does not replace the
component licenses.

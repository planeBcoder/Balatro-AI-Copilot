"""Read-only allowlist checks; no key access, network or personal log collection."""
import sys
from pathlib import Path
import zipfile

folder=Path(sys.argv[1])
package=folder/'Balatro-AI-Copilot-1.6.0-beta.1-Portable.zip'
with zipfile.ZipFile(package) as archive:
    names=archive.namelist()
    assert 'BalatroAICopilot.exe' in names
    assert '_internal/dependencies/steamodded-26.829.0.zip' in names
    assert any('LGPL-3.0-only' in n for n in names)
    for name in names:
        lower=name.lower()
        assert not lower.endswith(('.jkr','.dpapi','.log','.bak')),name
        assert Path(lower).name not in {'balatro.exe','settings.json'},name
        assert not any(part in lower.split('/') for part in ('live-training','backups','verification-v1.5')),name
        assert not any(q in lower for q in ('qt6virtualkeyboard','qt6qml','qt6pdf','qt6quick')),name
print('Public package audit passed: clean allowlist, licenses, pinned loaders, no game assets or personal files.')

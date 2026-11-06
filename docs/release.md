# Making a release

A release carries the Windows app and the PKHeX helper for each computer. `.github/workflows/release.yml` builds
both when a `v*` tag is pushed (or by hand from the Actions tab with a tag name) and creates the release as a
**draft**, marked pre-release for `v0.*`: nothing is public until it is published from the Releases page.

| File | What it is |
|---|---|
| `PokeUDS-windows.zip` | The app: unzip, run `PokeUDS/PokeUDS.exe` (`scripts/build_exe.py`) |
| `pokeuds-pkhex-<rid>[.exe]` | The PKHeX helper for each computer, which the app downloads (docs/pkhex.md) |
| `SHA256SUMS` | Checksums of all of the above; the app checks the helper against it |

The notes are `docs/releases/<tag>.md` when that file exists.

## The Windows app

```bash
pip install -r requirements-gui.txt pyinstaller
```

```bash
python scripts/build_exe.py
```

`flet pack` (PyInstaller) builds `dist/PokeUDS/` (the exe, Python, the game profiles, the strings, and Flet's
window, which the app unpacks to `%USERPROFILE%\.flet\client` on first start) and zips it to
`dist/PokeUDS-windows.zip`. The window is the exe's own: no console opens, and a trade runs as a second copy of the
exe (`PokeUDS.exe --cli trade ...`, `packaging/pokeuds_app.py`) started without a window, as `python -m pokeuds` is
from source. The exe's command-line mode is for the app; from a terminal use `python -m pokeuds`.

The icon is `packaging/PokeUDS.ico` when that file exists (or `--icon some.ico`), else Flet's default. A `.ico` with
16, 32, 48 and 256 px images covers the exe, the taskbar and the window.

## Checklist

1. `python -m pytest -q` passes; the version is the same in `pyproject.toml` and `pokeuds/__init__.py`.
2. `docs/releases/<tag>.md` says what is new and what is known not to work.
3. Build the app locally and start `dist/PokeUDS/PokeUDS.exe`: one window, no flashes, the Settings page shows the
   version; a demo trade from the Trade page (Debug's demo radio) completes.
4. Commit, then tag and push the tag (`git tag v0.1.0`, `git push origin v0.1.0`).
5. When the workflow is done, check the draft: the zip unzips and starts on a computer without Python, the PKHeX
   helper downloads from it (Settings, PKHeX helper, after publishing), SHA256SUMS lists every file.
6. Publish the draft.

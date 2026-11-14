# Making a release

A release carries the Windows app and the Mac app, each with PKHeX inside.
`.github/workflows/release.yml` builds them all when a `v*` tag is pushed (or by hand from the Actions tab with a tag
name) and creates the release as a **draft**, marked pre-release for `v0.*`: nothing is public until it is published
from the Releases page. Run by hand with no tag name, it only builds the apps and keeps them as the run's artifacts: a
test build, with no release.

| File | What it is |
|---|---|
| `PokeUDS-windows.zip` | The Windows app: unzip, run `PokeUDS/PokeUDS.exe` (`scripts/build_exe.py`) |
| `PokeUDS-macos-arm64.zip` | The Mac app for Apple Silicon: unzip, move `PokeUDS.app` to Applications (built on `macos-latest`) |
| `PokeUDS-macos-x64.zip` | The Mac app for Intel Macs (built on `macos-15-intel`) |
| `SHA256SUMS` | Checksums of the zips |

The notes are `docs/releases/<tag>.md` when that file exists (else just a title), followed by
`docs/releases/downloads.md`, which says which file to download. There is no Linux app: Linux runs from source.

## The Windows app

```bash
pip install -r requirements-gui.txt pyinstaller
```

```bash
python scripts/build_exe.py
```

`flet pack` (PyInstaller) builds `dist/PokeUDS/` (the exe, Python, the game profiles, the strings, and Flet's window)
and zips it to `dist/PokeUDS-windows.zip`. Flet would unpack its window into `%USERPROFILE%\.flet\client` on first
start; the build script unpacks it into `_internal/flet_view` instead, and the exe points Flet there
(`FLET_VIEW_PATH`), so the app is self-contained and writes nothing outside its folder: settings in `Data`, the
bank in `Bank`, received Pokemon in `received`. Unzip it somewhere writable; a packaged app in a folder it cannot
write reports that instead of falling back to the user profile. The
window is the exe's own: no console opens, and a trade runs as a second copy of the exe (`PokeUDS.exe --cli trade
...`, `packaging/pokeuds_app.py`) started without a window, as `python -m pokeuds` is from source. The exe's
command-line mode is for the app; from a terminal use `python -m pokeuds`.

The icon is `gui/assets/icon.ico` (or `--icon some.ico`), made from the artwork in `packaging/icon.jpg` by `python
scripts/make_icon.py`, which turns the artwork's red into the app's accent (the Start and Broadcast buttons' colour);
its 16 to 256 px images cover the exe, the taskbar and the window. `gui/assets/icon.png` is the logo at the top of
the rail. Both are bundled with the app.

## The Mac app

`python scripts/build_exe.py` on a Mac builds `dist/PokeUDS.app` and zips it with `ditto` to
`dist/PokeUDS-macos-arm64.zip` or `-x64.zip`, for the Mac it runs on (the release builds both). The app is a
one-folder bundle with PKHeX inside; its icon is `gui/assets/icon.icns`. Its bank is
`~/Library/Application Support/PokeUDS/bank`, not inside the bundle, which an update replaces whole. PyInstaller signs
it ad hoc; without an Apple Developer ID it is not notarized, so players allow it once (System Settings, Privacy &
Security, Open Anyway). The release workflow checks the signature, that its PKHeX answers, and that the
app's command line starts, but nothing opens its window there: start it on a Mac before publishing.

## Code signing (prepared, not in use)

PokeUDS is not code-signed yet: SignPath Foundation did not accept its application, and it is working towards it
([code_signing.md](code_signing.md)). The secret below is not set, so every release ships unsigned. The workflow is
ready for when signing is granted: the Windows app's own binaries, `PokeUDS.exe` and the PKHeX helper, would be
signed through SignPath. With the secret set, the release workflow builds `dist/PokeUDS` unzipped, uploads it, submits it
to SignPath (artifact configuration `pokeuds-windows`, `.github/signpath/artifact-configuration.xml`), waits for
the request to be approved, puts the signed folder in its place and only then zips it
(`python scripts/build_exe.py --zip-only`), so `SHA256SUMS` and the updater see the signed zip. It needs, in the
repository's Actions settings:

| | |
|---|---|
| secret `SIGNPATH_API_TOKEN` | a SignPath CI user's API token; without it the app ships unsigned, as before |
| variable `SIGNPATH_ORGANIZATION_ID` | the SignPath organization's id |
| variable `SIGNPATH_POLICY` | `release-signing` once SignPath Foundation grants it; unset, `test-signing` |

With signing in use, each release's signing request waits for an approver on SignPath before the workflow goes on.

## Checklist

1. `python -m pytest -q` passes; the version is the same in `pyproject.toml` and `pokeuds/__init__.py`.
2. `docs/releases/<tag>.md` says what is new and what is known not to work.
3. Build the app locally and start `dist/PokeUDS/PokeUDS.exe`: one window, no flashes, the Settings page shows the
   version; a demo trade from the Trade page (Debug's demo radio) completes.
4. Commit, then tag, and push `main` with the tag (`git tag v0.1.0`, `git push --atomic origin main v0.1.0`). Push
   `main` too: GitHub refuses to make a release from a commit whose workflow files differ from `main`'s.
5. When the workflow is done, check the draft: each zip unzips and starts on a computer without Python (the Mac app
   on a Mac, after Open Anyway), SHA256SUMS lists every file.
6. Publish the draft.

# Making a release

A release carries the Windows app and the Mac app, each with PKHeX inside.
`.github/workflows/release.yml` builds them all when a `v*` tag is pushed (or by hand from the Actions tab with a tag
name) and creates the release as a **draft**, marked pre-release for `v0.*`: nothing is public until it is published
from the Releases page. Run by hand with no tag name, it only builds the apps and keeps them as the run's artifacts: a
test build, with no release.

| File | What it is |
|---|---|
| `PokeUDS-windows.zip` | The Windows app: unzip, run `PokeUDS/PokeUDS.exe` (`scripts/build_exe.py`) |
| `PokeUDS-macos-arm64.zip` | The Mac app for Apple Silicon: unzip, move `PokeUDS.app` to Applications (built on `macos-15`, Apple Silicon) |
| `PokeUDS-macos-x64.zip` | The Mac app for Intel Macs (built on `macos-15-intel`) |
| `SHA256SUMS` | Checksums of the zips |

GitHub's Apple Silicon Mac runners are sometimes short: the arm64 job can wait a long time and then fail with "The job
was not acquired by Runner of type hosted", and the release job does not run (a release has every app or none). The
other builds carry on regardless; open the run and choose **Re-run failed jobs** to build the missing one, after
which the release job makes the draft.

The notes are `docs/releases/<tag>.md` when that file exists (else just a title), followed by
`docs/releases/downloads.md`, which says which file to download. There is no Linux app: Linux runs from source.

## The Windows app

```bash
pip install -r requirements-gui.txt pyinstaller
```

```bash
python scripts/build_exe.py
```

`flet pack` (PyInstaller) builds the app (Python, the game profiles, the strings, and Flet's window) flat into
`dist/PokeUDS/_internal`, with the app as `_internal/PokeUDS-app.exe`; the build compiles the launcher,
`dist/PokeUDS/PokeUDS.exe`, from `packaging/launcher` (it needs MSVC: Visual Studio or its Build Tools with "Desktop
development with C++"; GitHub's Windows runners have it), and zips the folder to `dist/PokeUDS-windows.zip`. Players
click the launcher, which finds the app even when moved out of the folder ([gui.md](gui.md#the-launcher)). Flet would unpack its window into `%USERPROFILE%\.flet\client` on first
start; the build script unpacks it into `_internal/flet_view` instead, and the exe points Flet there
(`FLET_VIEW_PATH`), so the app is self-contained and writes nothing outside its folder: settings in `Data`, the
bank in `Bank`, received Pokemon in `received`. Unzip it somewhere writable; a packaged app in a folder it cannot
write reports that instead of falling back to the user profile. The
window is the exe's own: no console opens, and a trade runs as a second copy of the app (`PokeUDS-app.exe --cli
trade ...`, `packaging/pokeuds_app.py`) started without a window, as `python -m pokeuds` is from source. The exe's
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
Security, Open Anyway). The bundle's own process never opens a window (Flet's client draws it, with its own Dock icon),
so the build marks it `LSUIElement`, a background app, and signs it again: without that the Dock showed a second
PokeUDS icon that bounced for a minute and then "Application Not Responding". The release workflow checks the
signature, that `LSUIElement` is set, that its PKHeX answers, and that the app's command line starts, but nothing
opens its window there: start it on a Mac before publishing.

`flet pack` runs through `scripts/pack_flet.py` (pokeldn's fix): Flet signs its own window again without the
entitlements its build gave it, and without `com.apple.security.files.user-selected.read-write` every file dialog
fails with `ENTITLEMENT_NOT_FOUND`. The wrapper seals the window's entitlements back in, and both the build and the
release workflow stop if the packed window lacks that one. The workflow also tries to install firmware to a board
that is not there and fails if that crashes instead of reporting the missing port.

## Code signing (prepared, not in use)

PokeUDS is not code-signed yet: SignPath Foundation did not accept its application, and it is working towards it
([code_signing.md](code_signing.md)). The secret below is not set, so every release ships unsigned. The workflow is
ready for when signing is granted: the Windows app's own binaries, `PokeUDS.exe` (the launcher),
`_internal/PokeUDS-app.exe` and the PKHeX helper, would be
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

## Translations

The app ships machine translations into the languages the 3DS games come in, listed in
`pokeuds/app/data/lang/languages.toml`; each made language is `pokeuds/app/data/lang/<locale>.toml`, and the Settings
menu offers it with a tag in that language ("Français · Traduction automatique"). They are made from the strings
template by Claude:

```bash
pip install -r requirements-translate.txt
```

```bash
python scripts/translate_strings.py
```

It needs Claude API credentials: `ANTHROPIC_API_KEY`, or a profile from `ant auth login` (Anthropic's API command
line). The Claude API is a Claude Console account, billed by use; a Claude subscription, the one Claude Code signs in
with, does not include it. Without one, a Claude Code session makes the translations instead:

```bash
python scripts/translate_strings.py --export translations
```

writes `translations/<locale>.json` for each language: the strings it lacks, each with an empty translation, and the
rules and glossary the API would get. Fill them in (the session does), then

```bash
python scripts/translate_strings.py --import translations
```

checks each translation as the API's are checked and adds it. Strings left empty are skipped, so a language can be
done a batch at a time: import after each batch, and the work is saved in `lang/<locale>.toml`; to carry on later,
export again, which lists only what is still missing. Settings offers a language once it covers 90% of the app
(`OFFERED_SHARE` in `pokeuds/app/i18n.py`), so one half done stays out of sight. A run sends only the strings
a language does not have yet (a release's changes: a few dozen), keeps every translation already in the file, so a
fix made by hand stays, and drops strings the app no longer shows. A translation whose `{value0}` fields differ from
the English is refused (twice, from the API) and left English; the run lists those. Without API credentials a
run stops at the first request and writes nothing. Official Pokemon terms come from
`scripts/translation_glossary.toml`. `--dry-run` says what a run would send; `--check` exits 1 when a language the
app ships is behind. The release workflow runs the tests with `POKEUDS_TRANSLATIONS_STRICT=1`, so a release with a
language behind the template fails before anything is built; without the variable that test is skipped, so a
contributor without credentials can still add strings.

A language pack for the same language lays over the built-in one (docs/plugins.md, Language packs).

## Checklist

1. `python -m pytest -q` passes; the version is the same in `pyproject.toml` and `pokeuds/__init__.py`. If strings
   changed, `python scripts/translate_strings.py` has run (`--check` passes).
2. `docs/releases/<tag>.md` says what is new and what is known not to work.
3. Build the app locally and start `dist/PokeUDS/PokeUDS.exe`: one window, no flashes, the Settings page shows the
   version; a demo trade from the Trade page (Debug's demo radio) completes. Copy `PokeUDS.exe` to the Desktop and
   start it there: the same app opens.
4. Commit, then tag, and push `main` with the tag (`git tag v0.1.0`, `git push --atomic origin main v0.1.0`). Push
   `main` too: GitHub refuses to make a release from a commit whose workflow files differ from `main`'s.
5. When the workflow is done, check the draft: each zip unzips and starts on a computer without Python (the Mac app
   on a Mac, after Open Anyway), SHA256SUMS lists every file.
6. Publish the draft.

## Which file do I download?

| Your computer | Download | Then |
|---|---|---|
| Windows | `PokeUDS-windows.zip` | Unzip it anywhere and run `PokeUDS/PokeUDS.exe`. |
| Mac with Apple Silicon (M1 or newer) | `PokeUDS-macos-arm64.zip` | Unzip it and move `PokeUDS.app` to Applications. |
| Intel Mac | `PokeUDS-macos-x64.zip` | Unzip it and move `PokeUDS.app` to Applications. |
| Linux | none | Run PokeUDS from source (README, "From source"). |

Not sure which Mac you have? Apple menu, About This Mac: "Chip: Apple M..." is Apple Silicon, "Processor: Intel" is Intel.

**First start on a Mac:** the app is not notarized by Apple, so macOS refuses it the first time. Open it once, then
go to System Settings, Privacy & Security, and click **Open Anyway** next to PokeUDS. After that it opens normally.

**The `pokeuds-pkhex-...` files are not for you to open.** They are the PKHeX helper for each kind of computer, and
the app downloads the right one by itself when it needs it (the apps above already carry their own copy). On a Mac
or Linux they have no file extension, as programs there don't, so they show as blank documents.

`SHA256SUMS` lists every file's checksum.

# Code signing policy (planned)

**PokeUDS is not code-signed yet.** It is working towards free code signing for open-source projects from
[SignPath Foundation](https://signpath.org); its first application was not accepted. Until signing is in place,
`PokeUDS-windows.zip` on the [Releases](https://github.com/Gr3nSkyDragon/PokeUDS/releases) page is unsigned, and
Windows may warn about an unknown publisher when the app first starts.

What signing would cover, once granted: an Authenticode signature on the binaries built from this repository's
source, `PokeUDS.exe` and the PKHeX helper `pokeuds-pkhex.exe`. GitHub Actions builds them from a `v*` tag of this
repository ([release.yml](https://github.com/Gr3nSkyDragon/PokeUDS/blob/main/.github/workflows/release.yml)), and
only the files that build produced would be signed, nothing built elsewhere. The workflow and the artifact
configuration
([.github/signpath/artifact-configuration.xml](https://github.com/Gr3nSkyDragon/PokeUDS/blob/main/.github/signpath/artifact-configuration.xml))
are ready; they sign nothing while the SignPath secret is not set. Third-party files bundled unchanged (Python,
Flet's window) keep their own publisher's signature or none. The Mac app would not be signed by SignPath.

## Team roles

| role | members |
|---|---|
| Committers and reviewers | [Gr3nSkyDragon](https://github.com/Gr3nSkyDragon) |
| Approvers | [Gr3nSkyDragon](https://github.com/Gr3nSkyDragon) |

Once signing is in place, every signing request for a release is approved by hand. Contributions from other people
reach the repository only as pull requests a committer reviews.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user
or the person installing or operating it. The requests it makes:

| when | to | what is sent |
|---|---|---|
| at launch, while Settings, Updates is on (the default), or on Check now | the GitHub API | a request for the latest releases; nothing about the player or their games |
| the player updates the app | GitHub | a download request |
| a Pokemon's sprite is shown that is not saved yet, while Settings, Pokemon sprites is on (the default) | GitHub ([PokeAPI/sprites](https://github.com/PokeAPI/sprites)) | a download request for that sprite |
| the player uses the GTS, and at launch while one of their listings or offers is open | public Nostr relays ([online.md](online.md)) | signed GTS events: listings, offers and answers, including the Pokemon traded |
| the player installs or updates a plugin, or browses a plugin source | the address of that plugin or source | a download request |
| the player clicks a link (the PokeLDN banner, a release page) | the player's web browser opens the page | what the browser sends |

Trades and Mystery Gift distributions go over local wireless to the player's own console, and the DS Wi-Fi Mystery
Gift server answers only the DS on the player's own network; neither reaches the internet. The board's firmware is
bundled with the app. GitHub's privacy statement covers requests to GitHub. Each relay operator's own policy covers
that relay. The app sends no analytics and no crash reports.

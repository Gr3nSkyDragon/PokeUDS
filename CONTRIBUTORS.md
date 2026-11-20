# Contributors

PokeUDS is built and tested on retail hardware by **Gr3nSkyDragon**.

**Claude Opus 5.5** (Anthropic) co-authored the rest of the repository: the Gen 6/7 and Virtual Console
trades, the app, the GTS, the DS Mystery Gift port, the packaging and most of the documentation. Those
commits carry `Co-authored-by: Claude Opus 5.5 <noreply@anthropic.com>`.

**DeepSeek Harness** (DeepSeek) is the AI coding agent that worked on the Gen 7 Mystery Gift support:
the distributor's beacon and OPSSR protocol reverse engineering, the signing implementation, the
signing-key extraction and Settings flow, the tests and the documentation. The Gen 7 commits carry
`Co-authored-by: DeepSeek Harness <noreply@deepseek.com>`.

**[Shinygati](https://github.com/Shinygati)** made the Gen 4/5 Mystery Gift over DS local wireless in their
[PokeLDN-Bluetooth](https://github.com/Shinygati/PokeLDN-Bluetooth) fork of pokeldn: the Gen 4 and Gen 5 encoders
and the DS beacon that PokeUDS's DS gift support (`pokeuds/games/ds/delivery.py`) is ported from (AGPL-3.0).

**[cerealcommas](https://github.com/cerealcommas)** added the ESP32-S2: its 3DS radio over TinyUSB in VC-Adapter's
esp32-uds-bridge and its Board page support here (download-mode installs, pokeldn's S2 left alone), tested first on a
Flipper Wi-Fi Developer Board (PokeUDS pull request #2, VC-Adapter pull request #1).

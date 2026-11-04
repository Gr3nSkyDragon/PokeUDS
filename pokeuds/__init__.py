"""PokeUDS: local wireless (UDS) trading with retail 3DS Pokemon games.

Layers, bottom up (see docs/architecture.md):

- ``pokeuds.radio``: raw 802.11 frame I/O through an ESP32-S3 or ldnd.exe.
- ``pokeuds.uds``: the 3DS UDS protocol (beacons, join handshake, CCMP, SecureData).
- ``pokeuds.games``: per-game protocols on top of UDS (Pokemon X/Y first).
- ``pokeuds.pk6``: Generation 6 Pokemon records.
- ``pokeuds.capture``: tools that turn emulator logs and sniffed traffic into transcripts.
"""

__version__ = "0.0.1"

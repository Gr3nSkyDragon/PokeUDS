"""A Nintendo Wi-Fi Connection stand-in for the DS games' Wi-Fi Mystery Gift (docs/ds_wifi.md).

A retail DS whose DNS points at this computer is answered here: ``dns`` sends Nintendo's names to this computer,
``server`` answers the connection test, the NAS login and the DLS1 gift download, and ``tls`` speaks the SSL 3.0 the
DS knows, with a certificate ``certs`` makes from the Wii's own client certificate (the "nds-constrain't" bug).

The protocol is dwc_network_server_emulator's (AGPL-3.0; polaris-, ToadKing, AdmiralCurtiss, msoucy, Sepalani),
read and written again here for one purpose, the gift download.
"""

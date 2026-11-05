"""Generation 7: Pokemon Sun/Moon and Ultra Sun/Ultra Moon.

Local trades start in the Festival Plaza (Link Trade) and carry ``.pk7`` records. Sun/Moon and
Ultra Sun/Ultra Moon trade with each other; neither trades with Generation 6 (Pokemon Bank moves
Pokemon forward instead). azahar_uds verified a trade between Ultra Sun and Ultra Moon.

Their code links the same local transport as Generation 6 (NEX PRUDP V1 behind Pia's local UDS
header, the same checksum key, gflnet), so ``pokeuds.nex`` already speaks it; the Festival Plaza
invitation and the trade's own messages wait for a capture (docs/captures.md).
"""

from ...pkx import PK7
from ..base import Game

SM = Game("sm", "Pokemon Sun/Moon", 7, PK7, "Festival Plaza",
          {"Sun": 0x0004000000164800, "Moon": 0x0004000000175E00}, {30: "Sun", 31: "Moon"}, ("usum",))
USUM = Game("usum", "Pokemon Ultra Sun/Ultra Moon", 7, PK7, "Festival Plaza",
            {"Ultra Sun": 0x00040000001B5000, "Ultra Moon": 0x00040000001B5100},
            {32: "Ultra Sun", 33: "Ultra Moon"}, ("sm",))

GAMES = (SM, USUM)

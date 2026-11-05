"""Building a Pokemon to send: the wishes the app collects, checked, and turned into a PKHeX request.

The PKHeX helper (pokeuds.pkhex, docs/pkhex.md) does the building and the legality check; this module
holds what the Trade page's builder and ``pokeuds build`` share: the games, the trainer as each generation
shows it, the limits on names, and keeping the result in the bank.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import species as species_names

GAMES = {
    "rby": ("Red / Blue / Yellow", 1, 151),
    "gsc": ("Gold / Silver / Crystal", 2, 251),
    "xy": ("X / Y", 6, 721),
    "oras": ("Omega Ruby / Alpha Sapphire", 6, 721),
    "sm": ("Sun / Moon", 7, 802),
    "usum": ("Ultra Sun / Ultra Moon", 7, 807),
}
NAME_LIMITS = {1: (10, 7), 2: (10, 7), 6: (12, 12), 7: (12, 12)}      # (nickname, OT) characters
ABILITIES = {"": "Any", "1": "First ability", "2": "Second ability", "hidden": "Hidden ability"}
GENDERS = {"": "Any", "male": "Male", "female": "Female"}


def generation(game: str) -> int:
    return GAMES[game][1]


# -- the trainer ----------------------------------------------------------------------------------

@dataclass
class Trainer:
    """The original trainer, kept as the games store it: a 16-bit TID and SID, and the OT's gender."""

    ot: str = "POKEUDS"
    tid: int = 12345
    sid: int = 54321
    female: bool = False

    def shown_id(self, gen: int) -> str:
        """The ID the game's summary screen shows: five digits, or six from Gen 7 on."""
        if gen >= 7:
            return f"{((self.sid << 16) | self.tid) % 1_000_000:06d}"
        return f"{self.tid:05d}"

    def g7_sid(self) -> int:
        """Gen 7's four-digit SID (what PKHeX shows; the game never does)."""
        return ((self.sid << 16) | self.tid) // 1_000_000

    def set_g7(self, shown: int, sid4: int) -> None:
        """From Gen 7's six-digit ID and four-digit SID."""
        if not 0 <= shown < 1_000_000 or not 0 <= sid4 <= 4294:
            raise ValueError("a Gen 7 ID is 0-999999 and its SID 0-4294")
        full = sid4 * 1_000_000 + shown
        if full >= 1 << 32:
            raise ValueError("that ID and SID do not fit (SID 4294 allows IDs up to 967295)")
        self.tid, self.sid = full & 0xFFFF, full >> 16

    def request(self, gen: int) -> dict:
        _, ot_limit = NAME_LIMITS[gen]
        return {"ot": self.ot[:ot_limit] or "POKEUDS", "tid": self.tid, "sid": self.sid if gen >= 3 else 0,
                "gender": 1 if self.female and gen >= 2 else 0}

    @classmethod
    def from_dict(cls, raw: dict | None, fallback_ot: str = "POKEUDS") -> "Trainer":
        raw = raw or {}
        return cls(str(raw.get("ot") or fallback_ot), int(raw.get("tid", 12345)) & 0xFFFF,
                   int(raw.get("sid", 54321)) & 0xFFFF, bool(raw.get("female", False)))

    def to_dict(self) -> dict:
        return {"ot": self.ot, "tid": self.tid, "sid": self.sid, "female": self.female}


# -- the wish -------------------------------------------------------------------------------------

@dataclass
class Wish:
    game: str = "xy"
    species: int = 25
    level: int = 50
    shiny: bool = False
    nature: int | None = None          # 0-24, Gen 3 on
    gender: str = ""                   # "", "male", "female"
    ability: str = ""                  # "", "1", "2", "hidden"; Gen 3 on
    held_item: int | None = None       # Gen 2 on
    ball: int | None = None            # Gen 3 on
    moves: list[int] = field(default_factory=list)
    nickname: str = ""

    def problems(self) -> list[str]:
        out = []
        if self.game not in GAMES:
            return [f"unknown game {self.game!r}"]
        name, gen, max_species = GAMES[self.game]
        if not 1 <= self.species <= max_species:
            out.append(f"{name} has species 1 to {max_species}")
        if not 1 <= self.level <= 100:
            out.append("the level is 1 to 100")
        nick_limit, _ = NAME_LIMITS[gen]
        if len(self.nickname) > nick_limit:
            out.append(f"a nickname in Generation {gen} is at most {nick_limit} characters")
        if len([m for m in self.moves if m]) > 4:
            out.append("four moves at most")
        if self.shiny and gen == 1:
            out.append("Generation 1 has no shiny Pokemon")
        return out

    def request(self, trainer: Trainer) -> dict:
        """Keyword arguments for ``pkhex.Helper.build``."""
        gen = generation(self.game)
        wish = {"level": self.level, "trainer": trainer.request(gen)}
        if self.shiny:
            wish["shiny"] = True
        if self.gender:
            wish["gender"] = self.gender
        if gen >= 3:
            if self.nature is not None:
                wish["nature"] = self.nature
            if self.ability:
                wish["ability"] = self.ability
            if self.ball:
                wish["ball"] = self.ball
        if gen >= 2 and self.held_item:
            wish["held_item"] = self.held_item
        if moves := [m for m in self.moves if m]:
            wish["moves"] = moves
        if self.nickname:
            wish["nickname"] = self.nickname
        return wish

    def label(self) -> str:
        return f"{species_names.name(self.species)} Lv {self.level} for {GAMES[self.game][0]}"


def build(wish: Wish, trainer: Trainer, helper=None):
    """Build with the PKHeX helper; raises ValueError with PKHeX's reason when nothing legal fits."""
    from .. import pkhex

    if problems := wish.problems():
        raise ValueError("; ".join(problems))
    helper = helper or pkhex.shared()
    return helper.build(wish.game, wish.species, **wish.request(trainer))


def keep(built, bank, wish: Wish):
    """Put a built record in the bank (source "built"), with PKHeX's verdict on it."""
    entry, _ = bank.deposit(built.data, built.extension.lstrip("."), source="built", note="",
                            extra={"built_for": wish.game})
    return bank.set_legality(entry, built.legal, built.report, built.encounter)

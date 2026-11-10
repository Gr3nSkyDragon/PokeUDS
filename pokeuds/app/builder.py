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
STATS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")      # Showdown's order, which every ivs/evs list here keeps
# Hidden Power's types, in the games' order (Fighting is 0).
HIDDEN_POWER = ("Fighting", "Flying", "Poison", "Ground", "Rock", "Bug", "Ghost", "Steel", "Fire", "Water", "Grass",
                "Electric", "Psychic", "Ice", "Dragon", "Dark")
GB_SHINY_ATTACK = (2, 3, 6, 7, 10, 11, 14, 15)          # a shiny Gen 2 Pokemon: these Attack DVs, the rest 10


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


# -- IVs, DVs and Hidden Power -------------------------------------------------------------------
#
# From Gen 3 on: six IVs of 0-31 and EVs of 0-252 (510 in all). In Gen 1/2: DVs of 0-15 for Atk, Def, Spe and Special
# (kept in SpA and SpD alike; the HP DV is made of the other four's lowest bits) and stat experience of 0-65535 in each
# stat, Special again in SpA and SpD. An IV of -1 is left to the encounter.


def stat_limits(gen: int) -> tuple[int, int]:
    """The highest IV (or DV) and EV (or stat experience) of a generation."""
    return (15, 65535) if gen <= 2 else (31, 252)


def gb_hp_dv(ivs: list[int]) -> int:
    """The HP DV a Gen 1/2 Pokemon gets from its Atk, Def, Spe and Special DVs (each unknown one taken as 15)."""
    atk, dfn, spc, spe = (15 if v < 0 else v for v in (ivs[1], ivs[2], ivs[3], ivs[5]))
    return ((atk & 1) << 3) | ((dfn & 1) << 2) | ((spe & 1) << 1) | (spc & 1)


def hidden_power(ivs: list[int] | None, gen: int) -> int | None:
    """Hidden Power's type for these IVs (an index of HIDDEN_POWER), or None while an IV it needs is left open."""
    if gen < 2 or not ivs:
        return None
    if gen == 2:                    # Gen 2: the two lowest bits of the Attack and Defense DVs
        if ivs[1] < 0 or ivs[2] < 0:
            return None
        return ((ivs[1] & 3) << 2) | (ivs[2] & 3)
    if any(v < 0 for v in ivs):
        return None
    hp, atk, dfn, spa, spd, spe = ivs  # the games' bit order is HP, Atk, Def, Spe, SpA, SpD
    bits = (hp & 1) | (atk & 1) << 1 | (dfn & 1) << 2 | (spe & 1) << 3 | (spa & 1) << 4 | (spd & 1) << 5
    return bits * 15 // 63


# The usual IVs for each type (Showdown's, and PKHeX's HiddenPower.DefaultLowBits): which IVs stay odd, one bit per
# stat in the games' order HP, Atk, Def, Spe, SpA, SpD. Ice keeps Speed and Special Attack at 31 and lowers Attack and
# Defense, as players expect.
HIDDEN_POWER_BITS = (0b000011, 0b001000, 0b001011, 0b001111, 0b010011, 0b011001, 0b011101, 0b011111, 0b100101,
                     0b101001, 0b101101, 0b101111, 0b110101, 0b111001, 0b111101, 0b111111)
_GAME_ORDER = (0, 1, 2, 5, 3, 4)            # the STATS index of each of HP, Atk, Def, Spe, SpA, SpD


def with_hidden_power(ivs: list[int] | None, kind: int, gen: int) -> list[int]:
    """IVs whose Hidden Power is ``kind``: an open IV is taken as the best (31, or a DV of 15), and only lowest bits
    move (31 becomes 30), into the usual spread for the type. Gen 2 moves the two lowest bits of Attack and Defense."""
    best, _ = stat_limits(gen)
    ivs = [best if v is None or v < 0 else v for v in (ivs or [-1] * 6)]
    if gen == 2:
        ivs[1] = (ivs[1] & ~3) | (kind >> 2)
        ivs[2] = (ivs[2] & ~3) | (kind & 3)
        ivs[4] = ivs[3]                                 # Special is one DV
        return ivs
    bits = HIDDEN_POWER_BITS[kind]
    for bit, stat in enumerate(_GAME_ORDER):
        if ivs[stat] & 1 != (bits >> bit) & 1:
            ivs[stat] += -1 if ivs[stat] & 1 else 1
    return ivs


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
    form: int = 0                      # an Alolan form, a Pikachu's cap...: set by a paste (Pasted.apply)
    ivs: list[int] | None = None       # STATS order; -1 is left to the encounter (Gen 1/2: DVs, see above)
    evs: list[int] | None = None       # STATS order (Gen 1/2: stat experience)

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
        out += self._stat_problems(gen)
        return out

    def _stat_problems(self, gen: int) -> list[str]:
        best_iv, best_ev = stat_limits(gen)
        out = []
        if self.ivs is not None:
            if len(self.ivs) != 6 or any(not -1 <= v <= best_iv for v in self.ivs):
                out.append(f"{'a DV' if gen <= 2 else 'an IV'} is 0 to {best_iv}")
            elif gen == 2 and self.shiny and any(v >= 0 for v in self.ivs[1:]):
                atk, dfn, spc, spe = self.ivs[1], self.ivs[2], self.ivs[3], self.ivs[5]
                if (atk >= 0 and atk not in GB_SHINY_ATTACK) or any(v >= 0 and v != 10 for v in (dfn, spc, spe)):
                    out.append("a shiny Gen 2 Pokemon has Defense, Speed and Special DVs of 10 and an Attack DV of "
                               "2, 3, 6, 7, 10, 11, 14 or 15")
        if self.evs is not None:
            if len(self.evs) != 6 or any(not 0 <= v <= best_ev for v in self.evs):
                out.append(f"{'stat experience' if gen <= 2 else 'an EV'} is 0 to {best_ev}")
            elif gen >= 3 and sum(self.evs) > 510:
                out.append("EVs total at most 510")
            elif gen <= 2 and any(self.evs) and not all(self.evs[:4] + self.evs[5:]):
                out.append("in Gen 1 and 2 every battle gives stat experience to all five stats: give each at least 1, "
                           "or leave them all at 0")
        return out

    def stats_request(self) -> dict:
        """The IVs and EVs part of the request (nothing when every one is left open)."""
        gen = generation(self.game)
        out = {}
        if self.ivs is not None and any(v >= 0 for v in self.ivs):
            ivs = list(self.ivs)
            if gen <= 2:
                ivs[0] = -1                     # the HP DV comes from the others
                ivs[4] = ivs[3]                 # Special is one DV
            out["ivs"] = ivs
        if self.evs is not None and any(self.evs):
            evs = list(self.evs)
            if gen <= 2:
                evs[4] = evs[3]
            out["evs"] = evs
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
        if self.form:
            wish["form"] = self.form
        wish.update(self.stats_request())
        return wish

    def label(self) -> str:
        return f"{species_names.name(self.species)} Lv {self.level} for {GAMES[self.game][0]}"


# -- a pasted set ---------------------------------------------------------------------------------

@dataclass
class Pasted:
    """One Showdown set from a paste (PKHeX's ``paste``), read for one game."""
    text: str
    species: int = 0
    species_name: str = ""
    form: int = 0
    form_name: str = ""
    fields: dict = field(default_factory=dict)      # what the wish takes: level, shiny, gender, nature, moves...
    names: dict = field(default_factory=dict)       # the names PKHeX gave them, for showing the set
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    usable: bool = False            # a species this game has: the rest can be fixed by hand after importing it

    @classmethod
    def from_reply(cls, raw: dict) -> "Pasted":
        fields = {k: raw[k] for k in ("level", "shiny", "gender", "nature", "ability", "held_item", "moves", "nickname",
                                      "ivs", "evs", "hidden_power")
                  if raw.get(k) not in (None, "", [])}
        names = {k: raw[k] for k in ("nature_name", "ability_name", "held_item_name", "move_names") if raw.get(k)}
        return cls(str(raw.get("text", "")), int(raw.get("species") or 0), str(raw.get("species_name") or ""),
                   int(raw.get("form") or 0), str(raw.get("form_name") or ""), fields, names,
                   [str(e) for e in raw.get("errors") or []], [str(n) for n in raw.get("notes") or []],
                   bool(raw.get("usable")))

    def title(self) -> str:
        name = self.species_name + (f"-{self.form_name}" if self.form_name else "")
        nick = self.fields.get("nickname")
        return (f"{nick} ({name})" if nick else name or "Unknown") + (f" @ {self.names['held_item_name']}"
                                                                         if self.names.get("held_item_name") else "")

    def summary(self) -> str:
        """What the import sets, on one line: level, shiny, nature, ability, moves."""
        parts = [f"Lv {self.fields.get('level', 100)}"]
        if self.fields.get("shiny"):
            parts.append("shiny")
        if self.fields.get("gender"):
            parts.append(self.fields["gender"])
        for key in ("nature_name", "ability_name"):
            if self.names.get(key):
                parts.append(self.names[key])
        if self.names.get("move_names"):
            parts.append(" / ".join(self.names["move_names"]))
        if (kind := self.fields.get("hidden_power")) is not None and 0 <= kind < len(HIDDEN_POWER):
            parts.append(f"Hidden Power {HIDDEN_POWER[kind]}")
        return ", ".join(parts)

    def apply(self, wish: Wish) -> Wish:
        """A wish for the same game with this set's species and wishes; what the set does not name is left open
        (the wish's ball stays: a Showdown set has none)."""
        f = self.fields
        return Wish(game=wish.game, species=self.species, level=int(f.get("level", 100)), shiny=bool(f.get("shiny")),
                    nature=f.get("nature"), gender=f.get("gender", ""), ability=f.get("ability", ""),
                    held_item=f.get("held_item"), ball=wish.ball, moves=list(f.get("moves", []))[:4],
                    nickname=str(f.get("nickname", ""))[:NAME_LIMITS[generation(wish.game)][0]], form=self.form,
                    ivs=list(f["ivs"]) if f.get("ivs") else None, evs=list(f["evs"]) if f.get("evs") else None)


def paste(game: str, text: str, helper=None) -> list[Pasted]:
    """The Showdown sets in ``text``, read for ``game`` by PKHeX."""
    from .. import pkhex

    if game not in GAMES:
        raise ValueError(f"unknown game {game!r}")
    helper = helper or pkhex.shared()
    return [Pasted.from_reply(raw) for raw in helper.paste(game, text)]


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

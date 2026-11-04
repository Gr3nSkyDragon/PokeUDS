"""What the app offers per game: which files it takes, what a run does, and the command it runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .settings import Settings


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    group: str
    console: str                 # what the 3DS runs, shown to the user
    extensions: tuple[str, ...]
    join_steps: str              # what the player does on the 3DS when it hosts and PokeUDS joins
    host_steps: str              # ... when PokeUDS hosts
    complete: bool               # the whole trade is implemented (VC) or only the session (3DS games)
    host_titles: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


ENTRIES = (
    Entry("rby", "Red / Blue / Yellow", "Game Boy (Virtual Console)", "a Gen 1 VC game", ("pk1", "pk2"),
          "On the 3DS, choose Host in the VC link menu, then go to the Pokemon Center's Trade Center and sit at the table. PokeUDS joins.",
          'On the 3DS, choose Join in the VC link menu once PokeUDS is waiting, then go to the Trade Center and sit at the table.',
          True, ("red", "blue", "yellow")),
    Entry("gsc", "Gold / Silver / Crystal", "Game Boy (Virtual Console)", "a Gen 2 VC game", ("pk2", "pk1"),
          "On the 3DS, choose Host in the VC link menu, then go to the Pokemon Center's Trade Center (or the Time Capsule for a .pk1) and sit at the table. PokeUDS joins.",
          'On the 3DS, choose Join in the VC link menu once PokeUDS is waiting, then go to the Trade Center (or the Time Capsule for a .pk1) and sit at the table.',
          True, ("gold", "silver", "crystal")),
    Entry("xy", "X / Y", "Generation 6", "Pokemon X or Y", ("pk6",),
          "On the 3DS, open the PSS with local wireless on. PokeUDS finds the console and joins its network.",
          "PokeUDS opens a network; on the 3DS, open the PSS with local wireless on.", False),
    Entry("oras", "Omega Ruby / Alpha Sapphire", "Generation 6", "Omega Ruby or Alpha Sapphire", ("pk6",),
          "On the 3DS, open the PSS with local wireless on. PokeUDS finds the console and joins its network.",
          "PokeUDS opens a network; on the 3DS, open the PSS with local wireless on.", False),
    Entry("sm", "Sun / Moon", "Generation 7", "Sun or Moon", ("pk7",),
          "On the 3DS, open the Festival Plaza and start a local Link Trade. PokeUDS joins its network.",
          "PokeUDS opens a network; on the 3DS, open the Festival Plaza and start a local Link Trade.", False),
    Entry("usum", "Ultra Sun / Ultra Moon", "Generation 7", "Ultra Sun or Ultra Moon", ("pk7",),
          "On the 3DS, open the Festival Plaza and start a local Link Trade. PokeUDS joins its network.",
          "PokeUDS opens a network; on the 3DS, open the Festival Plaza and start a local Link Trade.", False),
)
BY_KEY = {e.key: e for e in ENTRIES}


@dataclass
class Offer:
    path: str
    summary: str
    problem: str = ""
    generation: int = 0


def describe_offer(entry: Entry, path: str) -> Offer:
    """Load the file the user picked and say what it is, or why this game cannot take it."""
    suffix = Path(path).suffix.lower().lstrip(".")
    try:
        if entry.key in ("rby", "gsc"):
            from .. import gbpk

            record = gbpk.load(path)
            offer = Offer(path, record.describe(), generation=record.generation)
            console_gen = 1 if entry.key == "rby" else 2
            if record.generation == 2 and console_gen == 1 and (problems := gbpk.gen1_compatible(record)):
                offer.problem = "Through the Time Capsule: " + "; ".join(problems)
            return offer
        from .. import pkx
        from ..games import get

        record = pkx.load(path)
        s = pkx.summary(record)
        offer = Offer(path, f"species {s.species} '{s.nickname}', OT {s.ot_name}, PID {s.pid:08X}",
                      generation=get(entry.key).generation)
        try:
            pkx.check_format(record, get(entry.key).record)
        except ValueError as exc:
            offer.problem = str(exc)
        return offer
    except (OSError, ValueError) as exc:
        return Offer(path, "", problem=f"{Path(path).name}: {exc}" if suffix else str(exc))


def trade_mode(entry: Entry, offer: Offer) -> str:
    """How the trade will run, in words, for the VC games."""
    if entry.key == "rby":
        return "Gen 1 trade" if offer.generation == 1 else "Time Capsule: PokeUDS plays a Gen 2 game"
    if entry.key == "gsc":
        return "Gen 2 Trade Center, with mail" if offer.generation == 2 else "Time Capsule: PokeUDS plays a Gen 1 game"
    return "Joins or hosts the session and records the console (the trade messages are not mapped yet)"


def profile_gaps(entry: Entry) -> list[str]:
    if entry.complete:
        return []
    from ..games import profile

    return profile.load(entry.key).missing()


def command(entry: Entry, settings: Settings, *, offer: str, role: str, host_title: str = "", trades: int = 1,
            verbose: bool = False) -> list[str]:
    radio = settings.radio_spec()
    common = ["--radio", radio, "--offer", offer, "--output", settings.output, "--role", role,
              "--name", settings.trainer_name]
    if settings.keys and not radio.startswith("demo"):
        common += ["--keys", settings.keys]
    args = (["-v"] if verbose else [])
    if entry.key in ("rby", "gsc"):
        args += ["vc", "trade", *common, "--partner", "gen1" if entry.key == "rby" else "gen2", "--trades", str(trades)]
        if role == "host":
            args += ["--as", host_title or entry.host_titles[0]]
        return args
    return args + ["trade", entry.key, *common]


def missing_for_start(entry: Entry, settings: Settings, offer: Offer | None) -> list[str]:
    """Everything that stops a run from starting, in the words the app shows."""
    problems = []
    if not settings.radio_spec().startswith("demo") and (keys := settings.keys_problem()):
        problems.append(f"Console keys: {keys} (Settings)")
    if offer is None:
        problems.append("Choose a Pokemon file to offer")
    elif offer.problem:
        problems.append(offer.problem)
    for gap in profile_gaps(entry):
        problems.append(f"profiles/{entry.key}.toml has no {gap.split('.')[-1]} yet (needs captures)")
    return problems

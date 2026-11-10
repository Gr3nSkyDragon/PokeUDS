"""What the app offers per game: which files it takes, what a run does, and the command it runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..games import vc as vc_titles
from .settings import Settings


def _host_titles(generation: int) -> tuple[str, ...]:
    """The Virtual Console titles PokeUDS can stand in for when it hosts: English first, then the other languages."""
    found = [t for t in vc_titles.TITLES.values() if t.generation == generation]      # every language family
    return tuple(t.key for t in sorted(found, key=lambda t: (t.language != "en", t.language)))


def host_title_label(key: str) -> str:
    """"Red" or "Gold (German)", for the Trade page's "PokeUDS stands in for"."""
    title = vc_titles.TITLES.get(key)
    if title is None:
        return key.title()
    game = key.split("-")[0].title()
    return game if title.language == "en" else f"{game} ({vc_titles.LANGUAGES[title.language]})"


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
    max_queue: int = 6           # the most one session trades: a Game Boy party; a 3DS game's boxes and party


ENTRIES = (                    # the Trade page's order: Gen 6, Gen 7, then the Virtual Console
    Entry("xy", "X / Y", "Generation 6", "Pokemon X or Y", ("pk6",),
          "On the 3DS, open the PSS with local wireless on. Your trainer appears there: tap it and ask for a trade. PokeUDS answers and joins.",
          "On the 3DS, open the PSS with local wireless on. PokeUDS asks the first trainer it hears for a trade: accept it on the 3DS.",
          True, max_queue=31 * 30 + 6),
    Entry("oras", "Omega Ruby / Alpha Sapphire", "Generation 6", "Omega Ruby or Alpha Sapphire", ("pk6",),
          "On the 3DS, open the PSS with local wireless on. Your trainer appears there: tap it and ask for a trade. PokeUDS answers and joins.",
          "On the 3DS, open the PSS with local wireless on. PokeUDS asks the first trainer it hears for a trade: accept it on the 3DS.",
          True, max_queue=31 * 30 + 6),
    Entry("sm", "Sun / Moon", "Generation 7", "Sun or Moon", ("pk7",),
          "On the 3DS, open the Festival Plaza with local wireless on. PokeUDS appears among the guests: invite it to a Link Trade. PokeUDS accepts and the 3DS joins it.",
          "On the 3DS, open the Festival Plaza with local wireless on. PokeUDS invites the first player it hears to a Link Trade: accept it on the 3DS.",
          True, max_queue=32 * 30 + 6),
    Entry("usum", "Ultra Sun / Ultra Moon", "Generation 7", "Ultra Sun or Ultra Moon", ("pk7",),
          "On the 3DS, open the Festival Plaza with local wireless on. PokeUDS appears among the guests: invite it to a Link Trade. PokeUDS accepts and the 3DS joins it.",
          "On the 3DS, open the Festival Plaza with local wireless on. PokeUDS invites the first player it hears to a Link Trade: accept it on the 3DS.",
          True, max_queue=32 * 30 + 6),
    Entry("rby", "Red / Blue / Yellow", "Game Boy (Virtual Console)", "a Gen 1 VC game", ("pk1", "pk2"),
          "On the 3DS, choose Host in the VC link menu, then go to the Pokemon Center's Trade Center and sit at the table. PokeUDS joins.",
          'On the 3DS, choose Join in the VC link menu once PokeUDS is waiting, then go to the Trade Center and sit at the table.',
          True, _host_titles(1)),
    Entry("gsc", "Gold / Silver / Crystal", "Game Boy (Virtual Console)", "a Gen 2 VC game", ("pk2", "pk1"),
          "On the 3DS, choose Host in the VC link menu, then go to the Pokemon Center's Trade Center (or the Time Capsule for a .pk1) and sit at the table. PokeUDS joins.",
          'On the 3DS, choose Join in the VC link menu once PokeUDS is waiting, then go to the Trade Center (or the Time Capsule for a .pk1) and sit at the table.',
          True, _host_titles(2)),
)
BY_KEY = {e.key: e for e in ENTRIES}


@dataclass
class Offer:
    path: str
    summary: str
    problem: str = ""
    generation: int = 0
    dex: int = 0                 # for its sprite: species, form, shininess
    form: int = 0
    shiny: bool = False


def describe_offer(entry: Entry, path: str) -> Offer:
    """Load the file the user picked and say what it is, or why this game cannot take it."""
    offer = _describe_offer(entry, path)
    try:
        from .bank import parse

        parsed = parse(path)
        offer.dex, offer.form, offer.shiny = parsed.dex, parsed.form, parsed.shiny
    except (OSError, ValueError):
        pass
    return offer


def _describe_offer(entry: Entry, path: str) -> Offer:
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
        from .. import species as names

        offer = Offer(path, f"{names.name(s.species)} '{s.nickname}', OT {s.ot_name}, PID {s.pid:08X}",
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
    if entry.group == "Generation 6":
        return "PSS trade: PokeUDS offers right away and confirms as soon as the console does"
    if entry.complete:
        return "Festival Plaza Link Trade: PokeUDS offers right away and confirms as soon as the console does"
    return "Joins or hosts the session and records the console (the trade messages are not mapped yet)"


def profile_gaps(entry: Entry) -> list[str]:
    if entry.complete:
        return []
    from ..games import profile

    return profile.load(entry.key).missing()


def command(entry: Entry, settings: Settings, *, offer: str | list[str], role: str, host_title: str = "",
            trades: int | None = None, verbose: bool = False) -> list[str]:
    """The run for a trade; ``offer`` is one file or the trade queue in order (one trade each, unless ``trades``)."""
    radio = settings.radio_spec()
    queue = [offer] if isinstance(offer, str) else list(offer)
    common = ["--radio", radio, *[x for path in queue for x in ("--offer", path)], "--output", settings.output,
              "--role", role, "--name", settings.trainer_name]
    if settings.keys and not radio.startswith("demo"):
        common += ["--keys", settings.keys]
    args = (["-v"] if verbose else [])
    if entry.key in ("rby", "gsc"):
        args += ["vc", "trade", *common, "--partner", "gen1" if entry.key == "rby" else "gen2"]
        if trades is not None:
            args += ["--trades", str(trades)]
        if role == "host":
            args += ["--as", host_title or entry.host_titles[0]]
        return args
    if (entry.group == "Generation 6" or entry.complete) and trades is not None:
        common += ["--trades", str(trades)]
    return args + ["trade", entry.key, *common]


def queue_problems(entry: Entry, queue: list[Offer]) -> list[str]:
    """What is wrong with the trade queue as a whole (each offer's own problem aside)."""
    problems = []
    if len(queue) > entry.max_queue:
        problems.append(f"{entry.name} trades at most {entry.max_queue} Pokemon in one session"
                        + (" (a Game Boy party)" if entry.key in ("rby", "gsc") else "")
                        + f": the queue holds {len(queue)}")
    if entry.key in ("rby", "gsc") and len({o.generation for o in queue if not o.problem}) > 1:
        problems.append("A Virtual Console queue is all .pk1 or all .pk2: they trade in different rooms")
    return problems


def missing_for_start(entry: Entry, settings: Settings, offer: Offer | list[Offer] | None) -> list[str]:
    """Everything that stops a run from starting, in the words the app shows. ``offer``: one, or the queue."""
    problems = []
    if not settings.radio_spec().startswith("demo") and (keys := settings.keys_problem()):
        problems.append(f"Console keys: {keys} (Settings)")
    queue = [] if offer is None else [offer] if isinstance(offer, Offer) else list(offer)
    if not queue:
        problems.append("Choose a Pokemon to offer: from the bank, Build one, or a file")
    for one in queue:
        if one.problem:
            problems.append(one.problem)
    problems += queue_problems(entry, queue)
    for gap in profile_gaps(entry):
        problems.append(f"profiles/{entry.key}.toml has no {gap.split('.')[-1]} yet (needs captures)")
    return problems

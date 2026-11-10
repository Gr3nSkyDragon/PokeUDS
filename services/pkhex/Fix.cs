// When the build finds nothing legal (docs/pkhex.md, Fixing a build).
//
// Two kinds of help, both PKHeX.Core's own checks deciding:
//
// Repairs change nothing that was asked for. They give the Pokemon what a real one of its kind would have been
// through and the request cannot say: a trade (a Pokemon that evolves only by trade, Aromatisse or Gengar, must
// have been traded once; it is traded to a partner and back, so it is still the player's), the level its
// evolution needs (when no level was asked for), the relearnable moves its encounter teaches (Gen 6/7 egg and
// event moves), the memories Gen 6/7 record, a level above the one it was met at when it has EVs. Each repair
// is kept only when it clears the check it is for.
//
// The closest build is for a request no Pokemon can meet. The moves no encounter in the game can teach are
// dropped first, then the wishes least likely to matter are set free one at a time (ball, level, IVs, ability,
// gender, nature, held item, shininess, EVs) until a legal Pokemon is found, and then each is asked for again,
// kept when it still builds. What differs from the request is listed, and the caller decides.

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    sealed record Attempt(PKM Pk, LegalityAnalysis La, IEncounterable Encounter, List<string> Fixes);

    sealed class Outcome
    {
        public Attempt? Legal;
        public Attempt? Closest;          // the attempt with the fewest problems, to learn which moves fail
        public int Tried;
        public string? FirstProblem;
    }

    /// <summary>Whether the request names ``key`` (a nature of 0, Hardy, is one; a level or ball of 0 is none).</summary>
    static bool Asked(JsonObject request, string key) => key switch
    {
        "nature" => (int?)request[key] is >= 0,
        "level" or "ball" or "held_item" => (int?)request[key] is > 0,
        "ability" => (string?)request[key] is { Length: > 0 },
        "gender" => (string?)request[key] is "male" or "female",
        "shiny" => (bool?)request[key] == true,
        _ => request[key] is JsonArray,
    };

    static string SpeciesName(PKM pk) => Name(Strings.Species, pk.Species);

    // -- repairs --------------------------------------------------------------------------------

    static LegalityAnalysis Repair(Game game, ref PKM pk, JsonObject request, SimpleTrainerInfo trainer, List<string> fixes,
                                   bool evolved = false)
    {
        var la = new LegalityAnalysis(pk);
        for (var round = 0; round < 6 && !la.Valid; round++)
        {
            bool Fails(CheckIdentifier id) => la.Results.Any(r => !r.Valid && r.Identifier == id);

            // An evolved Pokemon below its evolution's level is not reported as an evolution problem: PKHeX finds no
            // encounter at all ("Unable to match an encounter"). For one evolved from its encounter, both are its
            // evolution's, and a repair must clear both.
            CheckIdentifier[] evolution = evolved ? [CheckIdentifier.Evolution, CheckIdentifier.Encounter] : [CheckIdentifier.Evolution];
            if (evolution.Any(Fails))
            {
                // A trade, the level, or both (Machamp: Machoke at Lv 28, then a trade): the first that clears it.
                var canTrade = pk is PK6 or PK7 && pk.HandlingTrainerName.Length == 0;
                var canLevel = !Asked(request, "level");
                if (canTrade && Try(ref pk, la, p => TradeThereAndBack(p, trainer), evolution) is { } traded)
                {
                    la = traded;
                    fixes.Add(Traded(pk));
                    continue;
                }
                if (canLevel && LowestLevel(ref pk, evolution) is { } raised)
                {
                    la = raised;
                    fixes.Add(Raised(pk));
                    continue;
                }
                if (canTrade && canLevel)
                {
                    var both = pk.Clone();
                    TradeThereAndBack(both, trainer);
                    if (LowestLevel(ref both, evolution) is { } tradedAndRaised && Count(tradedAndRaised) < Count(la))
                    {
                        pk = both;
                        la = tradedAndRaised;
                        fixes.Add(Traded(pk));
                        fixes.Add(Raised(pk));
                        continue;
                    }
                }
            }
            if (game.Generation >= 6 && (la.Info.Relearn.Any(m => !m.Valid) || la.Info.Moves.Any(m => !m.Valid))
                && Try(ref pk, la, p => MoveSetApplicator.SetRelearnMoves(p, new LegalityAnalysis(p))) is { } relearned)
            {
                la = relearned;
                var names = pk.RelearnMoves.Where(m => m != 0).Select(m => Name(Strings.Move, m));
                fixes.Add($"Gave it the relearnable moves its encounter teaches ({string.Join(", ", names)}): Gen 6 and 7 keep egg and event moves there.");
                continue;
            }
            if (Fails(CheckIdentifier.Memory) && pk is PK6 or PK7
                && Try(ref pk, la, p => { if (p is PK6 a) a.FixMemories(); else if (p is PK7 b) b.FixMemories(); }, CheckIdentifier.Memory) is { } remembered)
            {
                la = remembered;
                fixes.Add("Gave it the memories Gen 6 and 7 record for a Pokemon with its history.");
                continue;
            }
            if (Fails(CheckIdentifier.EVs) && !Asked(request, "level") && pk.CurrentLevel < 100
                && Try(ref pk, la, p => { p.CurrentLevel = (byte)(p.CurrentLevel + 1); p.ResetPartyStats(); }, CheckIdentifier.EVs) is { } battled)
            {
                la = battled;
                fixes.Add($"Raised to Lv {pk.CurrentLevel}: a Pokemon still at the level it was met at has not battled, so it cannot have EVs.");
                continue;
            }
            if (Fails(CheckIdentifier.Ball) && !Asked(request, "ball") && game.Generation >= 3
                && Try(ref pk, la, p => BallApplicator.ApplyBallLegalRandom(p), CheckIdentifier.Ball) is { } balled)
            {
                la = balled;
                fixes.Add($"Put it in a {Name(Strings.balllist, pk.Ball)}, a ball its encounter allows.");
                continue;
            }
            break;
        }
        return la;
    }

    static string Traded(PKM pk) =>
        $"Traded once to a partner and back: {SpeciesName(pk)} only evolves by trade, so it must have been traded.";

    static string Raised(PKM pk) => $"Raised to Lv {pk.CurrentLevel}: the lowest level {SpeciesName(pk)} can have evolved by.";

    static int Count(LegalityAnalysis la) => la.Results.Count(r => !r.Valid) + la.Info.Moves.Count(m => !m.Valid)
                                             + la.Info.Relearn.Count(m => !m.Valid);

    /// <summary>Change a copy; keep it (as ``pk``) only when it clears ``target`` (any check, when null) and leaves
    /// fewer problems.</summary>
    static LegalityAnalysis? Try(ref PKM pk, LegalityAnalysis la, Action<PKM> change, params CheckIdentifier[] targets)
    {
        var copy = pk.Clone();
        change(copy);
        copy.RefreshChecksum();
        var after = new LegalityAnalysis(copy);
        if (after.Results.Any(r => !r.Valid && targets.Contains(r.Identifier)) || Count(after) >= Count(la))
            return null;
        pk = copy;
        return after;
    }

    static void TradeThereAndBack(PKM pk, SimpleTrainerInfo trainer)
    {
        var partner = new SimpleTrainerInfo(trainer.Version)
        {
            OT = "PokeUDS", TID16 = (ushort)(trainer.TID16 ^ 0x5A5A), SID16 = (ushort)(trainer.SID16 ^ 0xA5A5),
            Language = trainer.Language, Gender = 0, Country = trainer.Country, Region = trainer.Region,
        };
        switch (pk)
        {
            case PK6 six:
                six.UpdateHandler(partner);
                six.UpdateHandler(trainer);
                break;
            case PK7 seven:
                seven.UpdateHandler(partner);
                seven.UpdateHandler(trainer);
                break;
        }
    }

    /// <summary>The lowest level above the current one that clears ``target``, set as ``pk``; null when none does.</summary>
    static LegalityAnalysis? LowestLevel(ref PKM pk, CheckIdentifier[] targets)
    {
        var start = pk;
        LegalityAnalysis? At(int level, out PKM copy)
        {
            copy = start.Clone();
            copy.CurrentLevel = (byte)level;
            copy.ResetPartyStats();
            copy.RefreshChecksum();
            var la = new LegalityAnalysis(copy);
            return la.Results.Any(r => !r.Valid && targets.Contains(r.Identifier)) ? null : la;
        }
        int low = pk.CurrentLevel + 1, high = 100;
        if (low > high || At(high, out _) is null)
            return null;
        while (low < high)                   // an evolution's level only ever has to be reached: the first that works
        {
            var mid = (low + high) / 2;
            if (At(mid, out _) is not null) high = mid; else low = mid + 1;
        }
        var found = At(low, out var best)!;
        pk = best;
        return found;
    }

    // -- the closest build ----------------------------------------------------------------------

    static readonly string[] Relaxable = ["ball", "level", "ivs", "ability", "gender", "nature", "held_item", "shiny", "evs"];

    static JsonObject? Closest(Game game, JsonObject request)
    {
        var working = request.DeepClone().AsObject();
        var trainer = Trainer(game, request);
        var blank = game.Blank();
        blank.Species = (ushort)(int)request["species"]!;
        blank.Form = (byte)((int?)request["form"] ?? 0);
        var moves = (request["moves"]?.AsArray() ?? []).Select(m => (ushort)(int)m!).Where(m => m != 0).ToList();
        bool Teachable(IEnumerable<ushort> set) =>
            EncounterMovesetGenerator.GenerateEncounters(blank, trainer, set.ToArray(), game.Versions).Any();
        // 1. Moves no encounter in the game teaches, alone, then the fewest more so that the rest go together.
        var kept = moves.Where(m => Teachable([m])).ToList();
        while (kept.Count > 0 && !Teachable(kept))
        {
            var drop = Enumerable.Range(0, kept.Count).FirstOrDefault(i => Teachable(kept.Where((_, j) => j != i)), kept.Count - 1);
            kept.RemoveAt(drop);
        }
        working["moves"] = new JsonArray(kept.Select(m => (JsonNode)(int)m).ToArray());

        // 2. Set wishes free until something builds.
        var freed = new List<string>();
        var outcome = Search(game, working, 60, 3);
        foreach (var key in Relaxable.Where(k => Asked(request, k)))
        {
            if (outcome.Legal is not null)
                break;
            working.Remove(key);
            freed.Add(key);
            outcome = Search(game, working, 60, 3);
        }
        // 3. Still nothing: drop the moves PKHeX finds invalid on the closest attempt.
        if (outcome.Legal is null && outcome.Closest is { } near)
        {
            var bad = near.La.Info.Moves.Select((m, i) => (m, i)).Where(x => !x.m.Valid).Select(x => near.Pk.GetMove(x.i)).ToHashSet();
            if (bad.Count > 0)
            {
                kept = kept.Where(m => !bad.Contains(m)).ToList();
                working["moves"] = new JsonArray(kept.Select(m => (JsonNode)(int)m).ToArray());
                outcome = Search(game, working, 60, 3);
            }
        }
        if (outcome.Legal is null)
            return null;
        // 4. Ask for each freed wish again; keep it when it still builds. IVs one at a time: an encounter that fixes
        // three at 31 (most legendaries) still takes the other three as asked.
        foreach (var key in freed.ToArray())
        {
            var trial = working.DeepClone().AsObject();
            trial[key] = request[key]!.DeepClone();
            var again = Search(game, trial, 60, 3);
            if (again.Legal is not null)
            {
                working = trial;
                outcome = again;
                freed.Remove(key);
            }
            else if (key == "ivs" && Stats(request, "ivs") is { } asked)
            {
                var ivs = new[] { -1, -1, -1, -1, -1, -1 };
                for (var i = 0; i < 6; i++)
                {
                    if (asked[i] < 0)
                        continue;
                    var keep = (int[])ivs.Clone();
                    keep[i] = asked[i];
                    var some = working.DeepClone().AsObject();
                    some["ivs"] = new JsonArray(keep.Select(v => (JsonNode)v).ToArray());
                    var partly = Search(game, some, 60, 3);
                    if (partly.Legal is not null)
                    {
                        ivs = keep;
                        working = some;
                        outcome = partly;
                    }
                }
            }
        }
        var legal = outcome.Legal;
        var reply = Describe(game, legal.Pk, legal.La, legal.Encounter);
        reply["fixes"] = new JsonArray(legal.Fixes.Select(f => (JsonNode)f).ToArray());
        reply["changes"] = new JsonArray(Changes(game, request, legal.Pk).Select(c => (JsonNode)c).ToArray());
        return reply;
    }

    /// <summary>What the built Pokemon has that differs from what was asked for, one line each.</summary>
    static List<string> Changes(Game game, JsonObject request, PKM pk)
    {
        var out_ = new List<string>();
        var asked = (request["moves"]?.AsArray() ?? []).Select(m => (ushort)(int)m!).Where(m => m != 0).ToList();
        var has = new[] { pk.Move1, pk.Move2, pk.Move3, pk.Move4 };
        foreach (var m in asked.Where(m => !has.Contains(m)))
            out_.Add($"Left out {Name(Strings.Move, m)}: no legal {SpeciesName(pk)} in {GameName(game)} knows it with the rest.");
        if ((int?)request["level"] is { } level and > 0 && pk.CurrentLevel != level)
            out_.Add($"Level {level} became {pk.CurrentLevel}.");
        if (game.Generation >= 3 && (int?)request["ball"] is { } ball and > 0 && pk.Ball != ball)
            out_.Add($"{Name(Strings.balllist, ball)} became {Name(Strings.balllist, pk.Ball)}: its encounter does not allow that ball.");
        if (game.Generation >= 3 && (int?)request["nature"] is { } nature and >= 0 && (int)pk.Nature != nature)
            out_.Add($"{Name(Strings.Natures, nature)} nature became {Name(Strings.Natures, (int)pk.Nature)}.");
        if (game.Generation >= 3 && (string?)request["ability"] is { Length: > 0 } slot)
        {
            var wanted = slot switch { "1" => 1, "2" => 2, "hidden" => 4, _ => 0 };
            if (wanted != 0 && pk.AbilityNumber != wanted)
                out_.Add($"Its ability became {Name(Strings.Ability, pk.Ability)}: no legal one has the ability asked for.");
        }
        if ((string?)request["gender"] is { } g && (g == "male" ? 0 : g == "female" ? 1 : -1) is var gender and >= 0 && pk.Gender != gender)
            out_.Add($"It is {(pk.Gender == 0 ? "male" : pk.Gender == 1 ? "female" : "genderless")}, not {g}.");
        if ((bool?)request["shiny"] == true && !pk.IsShiny)
            out_.Add("It is not shiny: no legal one can be.");
        if (game.Generation >= 2 && (int?)request["held_item"] is { } item and > 0 && pk.HeldItem != item)
            out_.Add($"It holds nothing instead of {Name(Strings.GetItemStrings(pk.Context, game.Versions[0]), item)}.");
        string[] stats = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"];
        if (Stats(request, "ivs") is { } ivs && pk is not GBPKM)
        {
            int[] got = [pk.IV_HP, pk.IV_ATK, pk.IV_DEF, pk.IV_SPA, pk.IV_SPD, pk.IV_SPE];
            var differ = Enumerable.Range(0, 6).Where(i => ivs[i] >= 0 && ivs[i] != got[i]).Select(i => $"{stats[i]} {ivs[i]}->{got[i]}").ToList();
            if (differ.Count > 0)
                out_.Add($"IVs changed ({string.Join(", ", differ)}): its encounter fixes some of them.");
        }
        if (Stats(request, "evs") is { } evs && pk is not GBPKM)
        {
            int[] got = [pk.EV_HP, pk.EV_ATK, pk.EV_DEF, pk.EV_SPA, pk.EV_SPD, pk.EV_SPE];
            if (!evs.SequenceEqual(got))
                out_.Add("Its EVs were left at 0: none legal fit.");
        }
        return out_;
    }

    static string GameName(Game game) => game.Key switch
    {
        "rby" => "Red/Blue/Yellow", "gsc" => "Gold/Silver/Crystal", "xy" => "X/Y", "oras" => "Omega Ruby/Alpha Sapphire",
        "sm" => "Sun/Moon", _ => "Ultra Sun/Ultra Moon",
    };
}

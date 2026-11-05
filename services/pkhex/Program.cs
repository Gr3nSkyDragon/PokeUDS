// PokeUDS's PKHeX helper. One JSON request per line on stdin, one JSON reply per line on stdout.
//
//   {"cmd":"ping"}                                   -> {"ok":true,"pkhex":"26.10.18.0"}
//   {"cmd":"lists","game":"xy"}                      -> species, moves, items, balls, natures, languages
//   {"cmd":"build","game":"xy","species":25,...}     -> a legal record (base64), its summary and report
//   {"cmd":"check","data":"<base64>","game":"xy"}    -> summary and legality report of a record
//   {"cmd":"gifts","game":"xy"}                      -> the official Mystery Gift cards PKHeX knows for the game
//   {"cmd":"gift","game":"xy","key":"..."|"data":..} -> a card (official, or a .wc6/.wc7/full file) made into its Pokemon
//   {"cmd":"card","game":"xy",...}                   -> a card from the editor's fields (CardEditor.cs), and what it gives
//   {"cmd":"card_fields","game":"xy","data":...}     -> any card (or an official key) as the editor's fields
//
// Every reply has "ok"; a failed request has "error" instead. Games: rby, gsc, xy, oras, sm, usum.
// Legality is PKHeX.Core's (LegalityAnalysis); the build tries the species' encounters in PKHeX's
// order and keeps the first that stays legal with everything asked for (docs/pkhex.md).

using System.Text.Json;
using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    sealed record Game(string Key, GameVersion[] Versions, Func<PKM> Blank, Func<SaveFile> Save, int Generation);

    static readonly Dictionary<string, Game> Games = new()
    {
        ["rby"] = new("rby", [GameVersion.RD, GameVersion.BU, GameVersion.YW], () => new PK1(), () => new SAV1(LanguageID.English, GameVersion.RD), 1),
        ["gsc"] = new("gsc", [GameVersion.GD, GameVersion.SI, GameVersion.C], () => new PK2(), () => new SAV2(LanguageID.English, GameVersion.C), 2),
        ["xy"] = new("xy", [GameVersion.X, GameVersion.Y], () => new PK6(), () => new SAV6XY(), 6),
        ["oras"] = new("oras", [GameVersion.OR, GameVersion.AS], () => new PK6(), () => new SAV6AO(), 6),
        ["sm"] = new("sm", [GameVersion.SN, GameVersion.MN], () => new PK7(), () => new SAV7SM(), 7),
        ["usum"] = new("usum", [GameVersion.US, GameVersion.UM], () => new PK7(), () => new SAV7USUM(), 7),
    };

    static readonly GameStrings Strings = GameInfo.GetStrings("en");

    static int Main()
    {
        // Wild, static, trade and egg encounters first; Mystery Gifts last, since an event's fixed PID,
        // shininess or OT rarely matches what was asked for.
        EncounterMovesetGenerator.PriorityList = [EncounterTypeGroup.Slot, EncounterTypeGroup.Static,
            EncounterTypeGroup.Trade, EncounterTypeGroup.Egg, EncounterTypeGroup.Mystery];
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        string? line;
        while ((line = Console.ReadLine()) is not null)
        {
            if (line.Trim().Length == 0)
                continue;
            JsonObject reply;
            try
            {
                var request = JsonNode.Parse(line)!.AsObject();
                reply = Handle(request);
                if (request["id"] is { } id)
                    reply["id"] = id.DeepClone();
            }
            catch (Exception e)
            {
                reply = new JsonObject { ["ok"] = false, ["error"] = e.Message };
            }
            Console.WriteLine(reply.ToJsonString());
            Console.Out.Flush();
        }
        return 0;
    }

    static JsonObject Handle(JsonObject request)
    {
        var cmd = (string?)request["cmd"] ?? throw new ArgumentException("no cmd");
        if (cmd == "ping")
            return new JsonObject { ["ok"] = true, ["pkhex"] = typeof(PKM).Assembly.GetName().Version!.ToString() };
        var game = Games.GetValueOrDefault((string?)request["game"] ?? "") ?? throw new ArgumentException("unknown game");
        return cmd switch
        {
            "lists" => Lists(game),
            "build" => Build(game, request),
            "check" => Check(game, Convert.FromBase64String((string)request["data"]!)),
            "gifts" => GiftList(game, (bool?)request["all"] ?? false),
            "gift" => Gift(game, request),
            "card" => BuildCard(game, request),
            "card_fields" => CardFieldsReply(game, request),
            _ => throw new ArgumentException($"unknown cmd {cmd}"),
        };
    }

    // -- lists ----------------------------------------------------------------------------------

    static JsonArray Combo(IEnumerable<ComboItem> items, Func<ComboItem, bool>? keep = null)
    {
        var array = new JsonArray();
        foreach (var item in items)
            if (keep is null || keep(item))
                array.Add(new JsonArray(item.Value, item.Text));
        return array;
    }

    static JsonObject Lists(Game game)
    {
        var sav = game.Save();
        var source = new FilteredGameDataSource(sav, GameInfo.Sources, false);
        return new JsonObject
        {
            ["ok"] = true,
            ["species"] = Combo(source.Species, s => s.Value > 0 && s.Value <= sav.MaxSpeciesID),
            ["moves"] = Combo(source.Moves, m => m.Value > 0),
            ["items"] = Combo(source.Items, i => i.Value > 0),
            ["balls"] = Combo(source.Balls, b => b.Value > 0),
            ["natures"] = game.Generation >= 3 ? Combo(source.Natures) : new JsonArray(),
            ["languages"] = Combo(source.Languages),
            // Where a Pokemon was met, and for an egg where it came from (the event places are 40001 on).
            ["locations"] = game.Generation >= 6 ? Combo(GameInfo.Sources.Met.GetLocationList(game.Versions[0], game.Generation == 6 ? EntityContext.Gen6 : EntityContext.Gen7, false)) : new JsonArray(),
            ["egg_locations"] = game.Generation >= 6 ? Combo(GameInfo.Sources.Met.GetLocationList(game.Versions[0], game.Generation == 6 ? EntityContext.Gen6 : EntityContext.Gen7, true)) : new JsonArray(),
            ["growth"] = new JsonArray(Enumerable.Range(0, sav.MaxSpeciesID + 1)
                .Select(i => (JsonNode)sav.Personal[(ushort)i].EXPGrowth).ToArray()),
        };
    }

    // -- build ----------------------------------------------------------------------------------

    static SimpleTrainerInfo Trainer(Game game, JsonObject request)
    {
        var t = request["trainer"]?.AsObject() ?? new JsonObject();
        return new SimpleTrainerInfo(game.Versions[0])
        {
            OT = (string?)t["ot"] ?? "POKEUDS",
            TID16 = (ushort)((int?)t["tid"] ?? 12345),
            SID16 = (ushort)((int?)t["sid"] ?? 54321),
            Language = (int?)t["language"] ?? (int)LanguageID.English,
            Gender = (byte)((int?)t["gender"] ?? 0),
        };
    }

    static EncounterCriteria Criteria(Game game, JsonObject request)
    {
        var c = EncounterCriteria.Unrestricted;
        if (game.Generation >= 3 && (int?)request["nature"] is { } nature and >= 0)
            c = c with { Nature = (Nature)nature };
        if ((string?)request["gender"] is { } g)
            c = c with { Gender = g == "male" ? Gender.Male : g == "female" ? Gender.Female : Gender.Random };
        if (game.Generation >= 3 && (string?)request["ability"] is { } a)
            c = c with { Ability = a switch
            {
                "1" => AbilityPermission.OnlyFirst, "2" => AbilityPermission.OnlySecond, "hidden" => AbilityPermission.OnlyHidden,
                _ => AbilityPermission.Any12H,
            } };
        if ((bool?)request["shiny"] == true)
            c = c with { Shiny = Shiny.Always };
        return c;
    }

    static JsonObject Build(Game game, JsonObject request)
    {
        var species = (ushort)(int)request["species"]!;
        var level = (int?)request["level"] ?? 0;
        if (level is < 0 or > 100)
            throw new ArgumentException("the level must be 1 to 100");
        var moves = (request["moves"]?.AsArray() ?? []).Select(m => (ushort)(int)m!).Where(m => m != 0).ToArray();
        var trainer = Trainer(game, request);
        var criteria = Criteria(game, request);
        var blank = game.Blank();
        blank.Species = species;
        blank.Form = (byte)((int?)request["form"] ?? 0);
        string? firstProblem = null;
        int tried = 0;
        foreach (var encounter in EncounterMovesetGenerator.GenerateEncounters(blank, trainer, moves, game.Versions).Take(200))
        {
            if (encounter is not IEncounterConvertible convertible)
                continue;
            if (level > 0 && encounter.LevelMin > level)
                continue;
            for (var roll = 0; roll < 6; roll++)
            {
                tried++;
                var pk = convertible.ConvertToPKM(trainer, criteria);
                if (pk.GetType() != blank.GetType())
                    break;
                if (pk.Species != species)
                {
                    // An egg or a pre-evolution met in the wild: evolve it, as the player would by levelling.
                    var nicknamed = pk.IsNicknamed;
                    pk.Species = species;
                    pk.Form = blank.Form;
                    if (!nicknamed)
                        pk.ClearNickname();
                }
                Apply(game, pk, request, moves, level);
                var la = new LegalityAnalysis(pk);
                if (la.Valid && Satisfied(pk, request, criteria))
                    return Describe(game, pk, la, encounter);
                firstProblem ??= la.Valid ? "the encounter could not meet every request" : la.Report(false);
            }
        }
        var reason = tried == 0 ? "no encounter of this species in the game fits the moves and level asked for"
                                : firstProblem ?? "no legal build found";
        return new JsonObject { ["ok"] = false, ["error"] = reason };
    }

    static void Apply(Game game, PKM pk, JsonObject request, ushort[] moves, int level)
    {
        if (level > 0 && level > pk.CurrentLevel)
            pk.CurrentLevel = (byte)level;
        if (moves.Length > 0)
        {
            var set = new ushort[4];
            moves.Take(4).ToArray().CopyTo(set, 0);
            pk.SetMoves(set);
            pk.HealPP();
        }
        else
        {
            // No moves asked for: what the Pokemon would know at its level, if that stays legal.
            pk.RefreshChecksum();
            var before = new[] { pk.Move1, pk.Move2, pk.Move3, pk.Move4 };
            var suggested = new ushort[4];
            MoveListSuggest.GetSuggestedCurrentMoves(new LegalityAnalysis(pk), suggested, MoveSourceType.LevelUp | MoveSourceType.Encounter);
            if (suggested.Any(m => m != 0))
            {
                pk.SetMoves(suggested);
                pk.HealPP();
                pk.RefreshChecksum();
                if (!new LegalityAnalysis(pk).Valid)
                {
                    pk.SetMoves(before);
                    pk.HealPP();
                }
            }
        }
        if (game.Generation >= 2 && (int?)request["held_item"] is { } item and > 0)
            pk.HeldItem = item;
        if (game.Generation >= 3 && (int?)request["ball"] is { } ball and > 0)
            pk.Ball = (byte)ball;
        if ((string?)request["nickname"] is { Length: > 0 } nick)
        {
            pk.SetNickname(nick);
        }
        // Some encounters ignore the criteria's Shiny (Gen 2 eggs and gifts, whose shininess is only a DV
        // pattern): make it shiny here and let the legality check refuse it where the game fixes it.
        if ((bool?)request["shiny"] == true && !pk.IsShiny)
            pk.SetShiny();
        pk.ResetPartyStats();
        pk.RefreshChecksum();
    }

    static bool Satisfied(PKM pk, JsonObject request, EncounterCriteria c)
    {
        if ((bool?)request["shiny"] == true && !pk.IsShiny)
            return false;
        if (c.IsSpecifiedNature() && pk.Format >= 3 && pk.Nature != c.Nature)
            return false;
        if ((int?)request["level"] is { } level and > 0 && pk.CurrentLevel != level)
            return false;
        return true;
    }

    // -- check and describe ---------------------------------------------------------------------

    static JsonObject Check(Game game, byte[] data)
    {
        var context = game.Generation switch
        {
            1 => EntityContext.Gen1, 2 => EntityContext.Gen2, 6 => EntityContext.Gen6, _ => EntityContext.Gen7,
        };
        var pk = EntityFormat.GetFromBytes(data, context)
                 ?? throw new ArgumentException("not a Pokemon record PKHeX can read");
        // A box record carries no battle stats; the party form Describe writes then gets them computed, as a game does
        // when it takes the Pokemon into the party (a Gen 6 trade sends the party form).
        if (pk.Stat_HPMax == 0)
            pk.ResetPartyStats();
        return Describe(game, pk, new LegalityAnalysis(pk), null);
    }

    static string Name(IReadOnlyList<string> names, int index) => index >= 0 && index < names.Count ? names[index] : index.ToString();

    static JsonObject Describe(Game game, PKM pk, LegalityAnalysis la, IEncounterable? built)
    {
        var data = new byte[pk.SIZE_PARTY];
        pk.WriteDecryptedDataParty(data);
        var moves = new JsonArray();
        foreach (var m in new[] { pk.Move1, pk.Move2, pk.Move3, pk.Move4 })
            if (m != 0)
                moves.Add(Name(Strings.Move, m));
        var ivs = new JsonArray(pk.IV_HP, pk.IV_ATK, pk.IV_DEF, pk.IV_SPA, pk.IV_SPD, pk.IV_SPE);
        var summary = new JsonObject
        {
            ["species"] = pk.Species,
            ["species_name"] = Name(Strings.Species, pk.Species),
            ["form"] = pk.Form,
            ["nickname"] = pk.Nickname,
            ["level"] = pk.CurrentLevel,
            ["shiny"] = pk.IsShiny,
            ["gender"] = pk.Gender switch { 0 => "male", 1 => "female", _ => "none" },
            ["ot_name"] = pk.OriginalTrainerName,
            ["tid"] = pk.TID16,
            // The ID the summary screen shows: five digits, six from Gen 7 on (PKHeX's DisplayTID).
            ["shown_id"] = pk.Format >= 7 ? pk.DisplayTID.ToString("000000") : pk.TID16.ToString("00000"),
            ["moves"] = moves,
            ["ivs"] = ivs,
            ["evs"] = new JsonArray(pk.EV_HP, pk.EV_ATK, pk.EV_DEF, pk.EV_SPA, pk.EV_SPD, pk.EV_SPE),
            ["stats"] = new JsonArray(pk.Stat_HPMax, pk.Stat_ATK, pk.Stat_DEF, pk.Stat_SPA, pk.Stat_SPD, pk.Stat_SPE),
            ["held_item"] = pk.Format >= 2 ? Name(Strings.Item, pk.HeldItem) : null,
            ["nature"] = pk.Format >= 3 ? Name(Strings.Natures, (int)pk.Nature) : null,
            ["ability"] = pk.Format >= 3 ? Name(Strings.Ability, pk.Ability) : null,
            ["ball"] = pk.Format >= 3 ? Name(Strings.balllist, pk.Ball) : null,
            ["origin"] = GameInfo.GetVersionName(pk.Version),
        };
        var encounter = la.EncounterMatch;
        return new JsonObject
        {
            ["ok"] = true,
            ["legal"] = la.Valid,
            ["report"] = la.Report(false),
            ["encounter"] = encounter is null ? null : LegalityFormatting.GetEncounterName(encounter, Strings.specieslist) + " " +
                                                       LegalityFormatting.GetEncounterLocation(encounter),
            ["extension"] = pk.Extension,
            ["data"] = Convert.ToBase64String(data),
            ["summary"] = summary,
        };
    }
}

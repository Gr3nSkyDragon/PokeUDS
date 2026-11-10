// PokeUDS's PKHeX helper. One JSON request per line on stdin, one JSON reply per line on stdout.
//
//   {"cmd":"ping"}                                   -> {"ok":true,"pkhex":"26.10.18.0"}
//   {"cmd":"lists","game":"xy"}                      -> species, moves, items, balls, natures, languages
//   {"cmd":"paste","game":"xy","text":"..."}         -> each Showdown set in the text as the builder's wishes
//   {"cmd":"build","game":"xy","species":25,...}     -> a legal record (base64), its summary and report
//   {"cmd":"check","data":"<base64>","game":"xy"}    -> summary and legality report of a record
//   {"cmd":"gifts","game":"xy"}                      -> the official Mystery Gift cards PKHeX knows for the game
//   {"cmd":"gift","game":"xy","key":"..."|"data":..} -> a card (official, or a .wc6/.wc7/full file) made into its Pokemon
//   {"cmd":"card","game":"xy",...}                   -> a card from the editor's fields (CardEditor.cs), and what it gives
//   {"cmd":"card_fields","game":"xy","data":...}     -> any card (or an official key) as the editor's fields
//   {"cmd":"migrate","game":"sm","from":"rby",...}   -> a Gen 1/2/6 record moved to Gen 7 as Transporter/Bank do (Migrate.cs)
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
            "paste" => Paste(game, request),
            "build" => Build(game, request),
            "check" => Check(game, Convert.FromBase64String((string)request["data"]!)),
            "gifts" => GiftList(game, (bool?)request["all"] ?? false),
            "gift" => Gift(game, request),
            "card" => BuildCard(game, request),
            "card_fields" => CardFieldsReply(game, request),
            "migrate" => Migrate(game, request),
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

    // -- paste ----------------------------------------------------------------------------------

    // Showdown or Smogon sets (a team export too), read by PKHeX's own parser in any language it knows, each as the
    // wishes `build` takes for this game. An error is something the set asks for that cannot be had here (a line the
    // parser cannot read, a species, form, move or item the game lacks, an ability the species cannot have); a note
    // is something the builder leaves to PKHeX (IVs and EVs, Hidden Power's type) or a generation does not have.
    static JsonObject Paste(Game game, JsonObject request)
    {
        var text = ((string?)request["text"] ?? "").Replace("\r", "");
        // The parser reads names (Gen 2's items are numbered apart) in the context of the most recent trainer.
        RecentTrainerCache.SetRecentTrainer(Trainer(game, request));
        var sav = game.Save();
        var source = new FilteredGameDataSource(sav, GameInfo.Sources, false);
        var items = new Dictionary<int, string>();                  // a game's list can name one item twice
        foreach (var item in source.Items.Where(i => i.Value > 0))
            items.TryAdd(item.Value, item.Text);
        var errorText = BattleTemplateParseErrorLocalization.Get("en");
        var gen = game.Generation;
        // A set is a run of lines between blank ones; a team export's "=== [gen7] Team ===" line would stop the
        // parser reading anything, so it goes.
        var blocks = new List<List<string>> { new() };
        foreach (var line in text.Split('\n'))
        {
            if (line.TrimStart().StartsWith("==="))
                continue;
            if (!string.IsNullOrWhiteSpace(line))
                blocks[^1].Add(line.Trim());
            else if (blocks[^1].Count > 0)
                blocks.Add([]);
        }
        var sets = new JsonArray();
        foreach (var block in blocks.Where(b => b.Count > 0))
        {
            ShowdownParsing.TryParseAnyLanguage(block, out var set);    // false still gives its best reading
            set ??= new ShowdownSet(block, BattleTemplateLocalization.Default);
            var errors = new JsonArray();
            var notes = new JsonArray();
            foreach (var invalid in set.InvalidLines)
                errors.Add(invalid.Humanize(errorText));
            var species = set.Species;
            if (species == 0)
            {
                if (errors.Count == 0)
                    continue;                                       // nothing in it at all
                errors.Insert(0, "The first line names no Pokemon.");
                sets.Add(new JsonObject { ["usable"] = false, ["errors"] = errors, ["notes"] = notes,
                                          ["text"] = string.Join("\n", block) });
                continue;
            }
            var speciesName = Name(Strings.Species, species);
            var reply = new JsonObject { ["species"] = species, ["species_name"] = speciesName,
                                         ["text"] = string.Join("\n", block) };
            if (species > sav.MaxSpeciesID)
            {
                errors.Add($"{speciesName} is not in this game.");
                reply["usable"] = false;
                reply["errors"] = errors;
                reply["notes"] = notes;
                sets.Add(reply);
                continue;
            }
            var form = set.Form;
            if (!sav.Personal.IsPresentInGame(species, form) || (form == 0 && set.FormName.Length > 0
                    && ShowdownParsing.GetFormFromString(set.FormName, Strings, species, sav.Context) == 0
                    && !string.Equals(set.FormName, ShowdownParsing.GetStringFromForm(0, Strings, species, sav.Context), StringComparison.OrdinalIgnoreCase)))
            {
                errors.Add($"{speciesName}-{set.FormName} is not in this game.");
                form = 0;
            }
            reply["form"] = form;
            reply["form_name"] = form == 0 ? "" : ShowdownParsing.GetStringFromForm(form, Strings, species, sav.Context);
            reply["nickname"] = set.Nickname is { Length: > 0 } nick && nick != speciesName ? nick : "";
            reply["level"] = Math.Clamp((int)set.Level, 1, 100);
            if (set.Shiny)
            {
                if (gen == 1)
                    notes.Add("Generation 1 has no shiny Pokemon.");
                else
                    reply["shiny"] = true;
            }
            if (set.Gender is 0 or 1)
            {
                if (gen == 1)
                    notes.Add("Generation 1 has no genders.");
                else
                    reply["gender"] = set.Gender == 0 ? "male" : "female";
            }
            if ((int)set.Nature < 25)
            {
                if (gen < 3)
                    notes.Add($"Natures start in Generation 3: {set.Nature} is left out.");
                else
                {
                    reply["nature"] = (int)set.Nature;
                    reply["nature_name"] = Name(Strings.Natures, (int)set.Nature);
                }
            }
            if (set.Ability > 0)
            {
                var abilityName = Name(Strings.Ability, set.Ability);
                if (gen < 3)
                    notes.Add($"Abilities start in Generation 3: {abilityName} is left out.");
                else
                {
                    var index = sav.Personal.GetFormEntry(species, (byte)form).GetIndexOfAbility(set.Ability);
                    if (index < 0)
                        errors.Add($"{speciesName} cannot have {abilityName} in this game.");
                    else
                    {
                        reply["ability"] = index switch { 0 => "1", 1 => "2", _ => "hidden" };
                        reply["ability_name"] = abilityName;
                    }
                }
            }
            if (set.HeldItem > 0)
            {
                if (gen == 1)
                    notes.Add("Generation 1 has no held items: the item is left out.");
                else if (!items.TryGetValue(set.HeldItem, out var itemName))
                    errors.Add($"{Name(Strings.Item, set.HeldItem)} is not in this game.");
                else
                {
                    reply["held_item"] = set.HeldItem;
                    reply["held_item_name"] = itemName;
                }
            }
            var moves = new JsonArray();
            var moveNames = new JsonArray();
            foreach (var move in set.Moves.Where(m => m != 0))
            {
                if (move > sav.MaxMoveID)
                    errors.Add($"{Name(Strings.Move, move)} is not in this game.");
                else
                {
                    moves.Add((int)move);
                    moveNames.Add(Name(Strings.Move, move));
                }
            }
            reply["moves"] = moves;
            reply["move_names"] = moveNames;
            PasteStats(game, set, reply, notes);
            reply["usable"] = true;             // what else is wrong can be fixed by hand once it is in the builder
            reply["errors"] = errors;
            reply["notes"] = notes;
            sets.Add(reply);
        }
        return new JsonObject { ["ok"] = true, ["sets"] = sets };
    }

    // A set's IVs and EVs (Showdown's order), as the builder takes them. From Gen 3 on a set without an IVs line means
    // 31s, as Showdown reads it (and any IVs are legal for nearly every Gen 6/7 encounter). Gen 1/2 sets carry DVs and
    // stat experience only when they name them (an IVs or EVs line, or Hidden Power): the DVs fix gender and shininess
    // too. PKHeX's parser already moved the IVs to the Hidden Power type a set names.
    static void PasteStats(Game game, ShowdownSet set, JsonObject reply, JsonArray notes)
    {
        var namedIVs = set.IVs.Any(v => v != 31) || set.HiddenPowerType >= 0;
        if (set.HiddenPowerType >= 0)
            reply["hidden_power"] = set.HiddenPowerType;
        if (game.Generation >= 3)
        {
            reply["ivs"] = new JsonArray(set.IVs[0], set.IVs[1], set.IVs[2], set.IVs[4], set.IVs[5], set.IVs[3]);
            reply["evs"] = new JsonArray(set.EVs[0], set.EVs[1], set.EVs[2], set.EVs[4], set.EVs[5], set.EVs[3]);
            return;
        }
        // PKHeX turns the set into DVs (31 is 15) and stat experience (an EV squared) when it applies it to a record.
        var pk = game.Blank();
        pk.Species = set.Species;
        pk.ApplySetDetails(set);
        var gb = (GBPKM)pk;
        if (namedIVs && game.Generation == 2 && set.Shiny)
            notes.Add("A shiny Gen 2 Pokemon's DVs are fixed: the set's DVs are left out.");
        else if (namedIVs)
            reply["ivs"] = new JsonArray(-1, gb.IV_ATK, gb.IV_DEF, gb.IV_SPC, gb.IV_SPC, gb.IV_SPE);
        // Showdown gives a Gen 1/2 set the most stat experience in every stat it does not name (an EV of 252), and a
        // battle feeds all five, so none is left at 0.
        static int Full(int named, int converted) => named > 0 ? converted : 65535;
        reply["evs"] = new JsonArray(Full(set.EVs[0], gb.EV_HP), Full(set.EVs[1], gb.EV_ATK), Full(set.EVs[2], gb.EV_DEF),
                                     Full(set.EVs[4], gb.EV_SPC), Full(set.EVs[4], gb.EV_SPC), Full(set.EVs[3], gb.EV_SPE));
        if (set.HiddenPowerType >= 0 && game.Generation == 1)
            notes.Add("Generation 1 has no Hidden Power.");
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
        if (game.Generation >= 3 && Stats(request, "ivs") is { } ivs)
            c = c with { IV_HP = (sbyte)ivs[0], IV_ATK = (sbyte)ivs[1], IV_DEF = (sbyte)ivs[2],
                         IV_SPA = (sbyte)ivs[3], IV_SPD = (sbyte)ivs[4], IV_SPE = (sbyte)ivs[5] };
        return c;
    }

    // IVs and EVs as Showdown orders them (HP, Atk, Def, SpA, SpD, Spe); an IV of -1 is left as the encounter rolled
    // it. Gen 1/2: DVs 0-15 for Atk, Def, Spe and Special (in SpA; the HP DV comes from the other four), and stat
    // experience 0-65535 (Special in SpA).
    static int[]? Stats(JsonObject request, string key)
    {
        if (request[key]?.AsArray() is not { } array)
            return null;
        if (array.Count != 6)
            throw new ArgumentException($"{key} takes six numbers: HP, Atk, Def, SpA, SpD, Spe");
        return array.Select(v => (int)v!).ToArray();
    }

    static void ApplyStats(Game game, PKM pk, JsonObject request)
    {
        var ivs = Stats(request, "ivs");
        var evs = Stats(request, "evs");
        if (pk is GBPKM gb)
        {
            if (ivs is not null)
            {
                if (ivs.Any(v => v > 15))
                    throw new ArgumentException("a DV is 0 to 15");
                if (ivs[1] >= 0) gb.IV_ATK = ivs[1];
                if (ivs[2] >= 0) gb.IV_DEF = ivs[2];
                if (ivs[5] >= 0) gb.IV_SPE = ivs[5];
                if (ivs[3] >= 0) gb.IV_SPC = ivs[3];
            }
            if (evs is not null)
            {
                if (evs.Any(v => v is < 0 or > 65535))
                    throw new ArgumentException("stat experience is 0 to 65535");
                (gb.EV_HP, gb.EV_ATK, gb.EV_DEF, gb.EV_SPE, gb.EV_SPC) = (evs[0], evs[1], evs[2], evs[5], evs[3]);
            }
            return;
        }
        if (ivs is not null)
        {
            if (ivs.Any(v => v > 31))
                throw new ArgumentException("an IV is 0 to 31");
            if (ivs[0] >= 0) pk.IV_HP = ivs[0];
            if (ivs[1] >= 0) pk.IV_ATK = ivs[1];
            if (ivs[2] >= 0) pk.IV_DEF = ivs[2];
            if (ivs[3] >= 0) pk.IV_SPA = ivs[3];
            if (ivs[4] >= 0) pk.IV_SPD = ivs[4];
            if (ivs[5] >= 0) pk.IV_SPE = ivs[5];
        }
        if (evs is not null)
        {
            if (evs.Any(v => v is < 0 or > 252) || evs.Sum() > 510)
                throw new ArgumentException("an EV is 0 to 252, and they total at most 510");
            (pk.EV_HP, pk.EV_ATK, pk.EV_DEF, pk.EV_SPA, pk.EV_SPD, pk.EV_SPE) = (evs[0], evs[1], evs[2], evs[3], evs[4], evs[5]);
        }
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
                    // The evolved species (or form: a Pikachu becomes an Alolan Raichu) has its own abilities; keep
                    // the slot the encounter rolled, not the pre-evolution's ability.
                    if (pk.Format >= 3)
                        pk.RefreshAbility(pk.AbilityNumber >> 1);
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
        if (reason.Contains("Cannot receive EVs"))
            reason += game.Generation <= 2
                ? " In Gen 1 and 2 every battle gives stat experience to all five stats: give each at least 1, or leave "
                  + "them all at 0."
                : " A Pokemon still at the level it was met at has not battled: raise the level, or leave the EVs at 0.";
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
        ApplyStats(game, pk, request);
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
            // Gen 2 numbers its items apart from the later games.
            ["held_item"] = pk.Format >= 2 ? Name(Strings.GetItemStrings(pk.Context, game.Versions[0]), pk.HeldItem) : null,
            ["nature"] = pk.Format >= 3 ? Name(Strings.Natures, (int)pk.Nature) : null,
            ["ability"] = pk.Format >= 3 ? Name(Strings.Ability, pk.Ability) : null,
            ["hidden_power"] = pk.Format >= 2 ? Name(Strings.types, pk.HPType + 1) : null,     // its type list opens with Normal
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

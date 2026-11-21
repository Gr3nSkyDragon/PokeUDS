// Moving a Pokemon to a later generation the way Nintendo's own apps do, with PKHeX.Core's copies of them:
//
//   {"cmd":"migrate","game":"sm","data":"<base64 .pk1/.pk2/.pk6>","from":"gsc","source":"crystal","trainer":{...}}
//     -> the .pk7, as "check" describes a record (legality, summary, data)
//
// Gen 1/2 -> Gen 7 is Poke Transporter's Virtual Console transfer (PK1/PK2.ConvertToPK7): the Pokemon is met in
// "Kanto"/"Johto" at its current level on today's date, in a Poke Ball, with three perfect IVs (five for Mew and
// Celebi), the nature EXP mod 25 gives, its hidden ability (except the species Transporter leaves on their
// first), EXP reset to the level's minimum, its moves (not Dizzy Punch) and nickname kept, and its held item and
// mail left behind. "source" is the VC game it is transferred from, as Transporter reads one game's save.
// Gen 6 -> Gen 7 is Pokemon Bank's withdrawal into Sun/Moon (PK6.ConvertToPK7). Both are one way.
//
// The trainer is who runs Transporter or Bank: they become the Pokemon's handling trainer, and their 3DS region
// its region. Nothing else is official: there is no way from Gen 1/2 to Gen 6, and none backwards.
//
//   {"cmd":"migrate","game":"swsh","data":"<base64 .pk7>","from":"sm","trainer":{...}}  -> the .pk8
//
// Gen 7 -> Sword/Shield is Pokemon HOME's (PK7.ConvertToPK8), for the species and forms Sword/Shield have
// ("present" lists them). The origin game stays as it was, and with it the origin mark the Switch games show: the
// Game Boy for a Virtual Console Pokemon, the pentagon for a Gen 6 one, the clover for a Gen 7 one. HOME gives it
// a tracker (a random number here: none can be had from HOME itself) and the Sword/Shield trainer who takes it out
// becomes its handling trainer. A Gen 1/2/6 Pokemon goes through Gen 7 first, as it went through Bank to HOME.

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    static readonly Dictionary<string, GameVersion> VcSources = new()
    {
        ["red"] = GameVersion.RD, ["green"] = GameVersion.GN, ["blue"] = GameVersion.BU, ["yellow"] = GameVersion.YW,
        ["gold"] = GameVersion.GD, ["silver"] = GameVersion.SI, ["crystal"] = GameVersion.C,
    };

    static readonly object MigrateLock = new();

    static int? VcLanguage(string? code) => code switch
    {
        "jp" => (int)LanguageID.Japanese, "en" => (int)LanguageID.English, "fr" => (int)LanguageID.French,
        "it" => (int)LanguageID.Italian, "de" => (int)LanguageID.German, "es" => (int)LanguageID.Spanish,
        "kr" => (int)LanguageID.Korean,
        null or "" => null,
        _ => throw new ArgumentException($"unknown language {code}"),
    };

    static JsonObject Migrate(Game game, JsonObject request)
    {
        if (game.Generation == 8)
            return MigrateHome(game, request);
        if (game.Generation != 7)
            throw new ArgumentException("Pokemon move to Generation 7 or to Sword/Shield only (Transporter, Bank and HOME go nowhere else)");
        var from = Games.GetValueOrDefault((string?)request["from"] ?? "") ?? throw new ArgumentException("unknown source game");
        var context = from.Generation switch
        {
            1 => EntityContext.Gen1, 2 => EntityContext.Gen2, 6 => EntityContext.Gen6,
            _ => throw new ArgumentException("only Gen 1, Gen 2 and Gen 6 Pokemon migrate to Gen 7"),
        };
        var pk = EntityFormat.GetFromBytes(Convert.FromBase64String((string)request["data"]!), context)
                 ?? throw new ArgumentException("not a Pokemon record PKHeX can read");
        if (pk.IsEgg)
            throw new ArgumentException("an Egg cannot be moved: hatch it first");

        var t = request["trainer"]?.AsObject() ?? new JsonObject();
        var trainer = new SimpleTrainerInfo(game.Versions[0])
        {
            OT = (string?)t["ot"] ?? "POKEUDS",
            TID16 = (ushort)((int?)t["tid"] ?? 12345),
            SID16 = (ushort)((int?)t["sid"] ?? 54321),
            Gender = (byte)((int?)t["gender"] ?? 0),
            // A Gen 1/2 record has no language of its own: Transporter gives it the language of the Virtual Console
            // title it came from, which PKHeX takes from the trainer doing the transfer. `language` is that title's
            // ("de", "jp"...), when the bank knows it; else the trainer's, else English.
            Language = VcLanguage((string?)request["language"]) ?? (int?)t["language"] ?? (int)LanguageID.English,
            Generation = 7,
            Context = EntityContext.Gen7,
        };

        PKM? moved;
        EntityConverterResult result;
        lock (MigrateLock)          // the converter reads these from statics
        {
            var (vc1, vc2) = (EntityConverter.VirtualConsoleSourceGen1, EntityConverter.VirtualConsoleSourceGen2);
            try
            {
                var source = (string?)request["source"];
                if (source is not null && !VcSources.ContainsKey(source))
                    throw new ArgumentException($"unknown Virtual Console game {source}");
                if (from.Generation == 1)
                {
                    var v = source is null ? GameVersion.RD : VcSources[source];
                    if (v is not (GameVersion.RD or GameVersion.GN or GameVersion.BU or GameVersion.YW))
                        throw new ArgumentException($"{source} is not a Generation 1 game");
                    // International Blue is the game PKHeX calls GN (Japan's Green); BU is Japan's Blue.
                    if (v == GameVersion.BU && pk.Japanese == false)
                        v = GameVersion.GN;
                    EntityConverter.VirtualConsoleSourceGen1 = v;
                }
                else if (from.Generation == 2)
                {
                    var v = source is null ? GameVersion.GD : VcSources[source];
                    if (v is not (GameVersion.GD or GameVersion.SI or GameVersion.C))
                        throw new ArgumentException($"{source} is not a Generation 2 game");
                    EntityConverter.VirtualConsoleSourceGen2 = v;
                }
                RecentTrainerCache.SetRecentTrainer(trainer);
                moved = EntityConverter.ConvertToType(pk, typeof(PK7), out result);
            }
            finally
            {
                (EntityConverter.VirtualConsoleSourceGen1, EntityConverter.VirtualConsoleSourceGen2) = (vc1, vc2);
            }
        }
        if (moved is not PK7 pk7)
            throw new ArgumentException(result switch
            {
                EntityConverterResult.IncompatibleForm => "Pokemon Bank does not take this form into Generation 7 (a Cosplay Pikachu stays in Gen 6)",
                EntityConverterResult.IncompatibleSpecies => "this species cannot move to Generation 7",
                _ => $"PKHeX could not move it ({result})",
            });
        if (from.Generation <= 2)
        {
            // A Virtual Console title is sold in one console region, and PKHeX checks the language against it: German
            // and Italian only on a European 3DS, Japanese on a Japanese one, Korean on a Korean one. The transfer was
            // made as an Americas console (English, French, Spanish); give the record the console its language needs.
            var (console, country) = (LanguageID)pk7.Language switch
            {
                LanguageID.Japanese => (0, 1),          // Japan
                LanguageID.Korean => (5, 136),          // Korea, South Korea
                LanguageID.German => (2, 78),           // Europe, Germany
                LanguageID.Italian => (2, 83),          // Europe, Italy
                _ => ((int)pk7.ConsoleRegion, (int)pk7.Country),
            };
            if (console != pk7.ConsoleRegion)
            {
                pk7.ConsoleRegion = (byte)console;
                pk7.Country = (byte)country;
                pk7.Region = 0;
                pk7.RefreshChecksum();
            }
        }
        if (from.Generation == 6)
        {
            // Withdrawn from Bank, a Gen 6 Pokemon is handled by the trainer who withdrew it, even its own OT (PKHeX
            // requires it of every Gen 6-origin Gen 7 record): what PK7's trade-to-handler does, which is not public.
            if (pk7.HandlingTrainerName != trainer.OT)
            {
                pk7.HandlingTrainerName = trainer.OT;
                pk7.HandlingTrainerFriendship = pk7.PersonalInfo.BaseFriendship;
                pk7.HandlingTrainerAffection = 0;
            }
            pk7.HandlingTrainerGender = trainer.Gender;
            pk7.CurrentHandler = 1;
            pk7.RefreshChecksum();
        }
        if (pk7.Stat_HPMax == 0)
            pk7.ResetPartyStats();
        return Describe(game, pk7, new LegalityAnalysis(pk7), null);
    }

    // -- Gen 7 to Sword/Shield, as Pokemon HOME --------------------------------------------------

    static JsonObject Present(Game game)
    {
        if (game.Generation != 8)
            throw new ArgumentException("present lists what Sword/Shield take");
        var present = new JsonArray();
        for (ushort species = 1; species <= 807; species++)
        {
            var forms = new JsonArray();
            for (byte form = 0; form < PersonalTable.USUM[species].FormCount; form++)
                if (PersonalTable.SWSH.IsPresentInGame(species, form))
                    forms.Add(form);
            if (forms.Count > 0)
                present.Add(new JsonArray(species, forms));
        }
        return new JsonObject { ["ok"] = true, ["present"] = present };
    }

    static JsonObject MigrateHome(Game game, JsonObject request)
    {
        var from = Games.GetValueOrDefault((string?)request["from"] ?? "") ?? throw new ArgumentException("unknown source game");
        if (from.Generation != 7)
            throw new ArgumentException("only a Gen 7 Pokemon goes to Sword/Shield here: move a Gen 1, 2 or 6 one to Gen 7 first");
        var pk = EntityFormat.GetFromBytes(Convert.FromBase64String((string)request["data"]!), EntityContext.Gen7) as PK7
                 ?? throw new ArgumentException("not a Gen 7 record PKHeX can read");
        if (pk.IsEgg)
            throw new ArgumentException("an Egg cannot be moved: hatch it first");
        if (!PersonalTable.SWSH.IsPresentInGame(pk.Species, pk.Form))
            throw new ArgumentException(PersonalTable.SWSH.IsPresentInGame(pk.Species, 0)
                ? "Sword and Shield do not have this form"
                : $"{Name(Strings.Species, pk.Species)} is not in Sword or Shield");

        var t = request["trainer"]?.AsObject() ?? new JsonObject();
        var trainer = new SimpleTrainerInfo(GameVersion.SW)
        {
            OT = (string?)t["ot"] ?? "POKEUDS",
            TID16 = (ushort)((int?)t["tid"] ?? 12345),
            SID16 = (ushort)((int?)t["sid"] ?? 54321),
            Gender = (byte)((int?)t["gender"] ?? 0),
            Language = (int?)t["language"] ?? (int)LanguageID.English,
            Generation = 8,
            Context = EntityContext.Gen8,
        };
        PKM? moved;
        EntityConverterResult result;
        lock (MigrateLock)
        {
            RecentTrainerCache.SetRecentTrainer(trainer);
            moved = EntityConverter.ConvertToType(pk, typeof(PK8), out result);
        }
        if (moved is not PK8 pk8)
            throw new ArgumentException(result switch
            {
                EntityConverterResult.IncompatibleForm => "Sword and Shield do not have this form",
                EntityConverterResult.IncompatibleSpecies => "this species cannot go to Sword or Shield",
                _ => $"PKHeX could not move it ({result})",
            });
        if (pk8.Tracker == 0)
            pk8.Tracker = ((ulong)(uint)Random.Shared.Next() << 32 | (uint)Random.Shared.Next()) | 1;
        HomeHandler(pk8, trainer);
        if (pk8.Stat_HPMax == 0)
            pk8.ResetPartyStats();
        pk8.RefreshChecksum();
        return Describe(game, pk8, new LegalityAnalysis(pk8), null);
    }

    // Taken out of HOME into Sword/Shield by `trainer`: its handling trainer, even its own OT (PKHeX requires a handler
    // of every Pokemon from before Gen 8), with what the previous handler left behind cleared as HOME clears it.
    static void HomeHandler(PK8 pk8, SimpleTrainerInfo trainer)
    {
        if (pk8.HandlingTrainerName != trainer.OT)
        {
            pk8.HandlingTrainerName = trainer.OT;
            pk8.HandlingTrainerFriendship = pk8.PersonalInfo.BaseFriendship;
        }
        pk8.HandlingTrainerGender = trainer.Gender;
        pk8.HandlingTrainerLanguage = (byte)trainer.Language;
        pk8.HandlingTrainerMemory = 0;
        pk8.HandlingTrainerMemoryIntensity = 0;
        pk8.HandlingTrainerMemoryFeeling = 0;
        pk8.HandlingTrainerMemoryVariable = 0;
        pk8.CurrentHandler = 1;
    }
}

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

    static JsonObject Migrate(Game game, JsonObject request)
    {
        if (game.Generation != 7)
            throw new ArgumentException("Pokemon move to Generation 7 only (Transporter and Bank go nowhere else)");
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
            Language = (int?)t["language"] ?? (int)LanguageID.English,
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
}

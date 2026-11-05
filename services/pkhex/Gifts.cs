// Mystery Gift cards for Gen 6 and 7 (docs/mystery_gift.md): the official cards PKHeX.Core embeds, a card opened
// from a file, and custom cards built from a form. Every card can be made into the Pokemon it gives, as the
// requested trainer would receive it, and checked; a custom card's Pokemon matches no real event, so PKHeX's
// check refuses it ("custom" says so).
//
// Card files: a .wc6 / .wc7 is the 264-byte card; a full file (.wc6full / .wc7full) is 784 bytes, a 520-byte
// distribution header (RestrictVersion at 0x000, RestrictLanguage at 0x1FF, the rest not mapped) then the card.

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    const int CardSize = 0x108, FullSize = 0x310, FullCardAt = 0x208;

    // RestrictVersion bits: 1 X / Sun, 2 Y / Moon, 4 Alpha Sapphire / Ultra Sun, 8 Omega Ruby / Ultra Moon.
    static readonly Dictionary<GameVersion, int> VersionBit = new()
    {
        [GameVersion.X] = 1, [GameVersion.Y] = 2, [GameVersion.AS] = 4, [GameVersion.OR] = 8,
        [GameVersion.SN] = 1, [GameVersion.MN] = 2, [GameVersion.US] = 4, [GameVersion.UM] = 8,
    };

    static MysteryGift[] Table(Game game) => game.Generation switch
    {
        6 => EncounterEvent.MGDB_G6,
        7 => EncounterEvent.MGDB_G7,
        _ => throw new ArgumentException("Mystery Gift cards are Gen 6 and 7 here (Gen 2's went by infrared)"),
    };

    static bool ForGame(Game game, MysteryGift gift) => game.Versions.Any(v => Reaches(gift, v));

    static bool Reaches(MysteryGift gift, GameVersion version) => gift switch
    {
        WC6 w => w.CanBeReceivedByVersion(version),
        WC7 w => w.CanBeReceivedByVersion(version),
        _ => true,
    };

    static string GiftKey(MysteryGift g) => $"{g.CardID:0000}-{g.Species}-{g.Form}";

    // -- listing ------------------------------------------------------------------------------

    // Pokemon cards, one per key; with "all", every card for the game (items, revisions), each with its bytes.
    static JsonObject GiftList(Game game, bool all)
    {
        var list = new JsonArray();
        var seen = new HashSet<string>();
        foreach (var gift in Table(game).Where(g => (all || g.IsEntity) && ForGame(game, g)).OrderByDescending(g => g.CardID))
        {
            var key = GiftKey(gift);
            if (!seen.Add(key) && !all)
                continue;
            var info = CardInfo(game, gift, key);
            Undated(info);
            if (all && gift is DataMysteryGift data)
                info["card_data"] = Convert.ToBase64String(data.Data.ToArray());
            list.Add(info);
        }
        return new JsonObject { ["ok"] = true, ["gifts"] = list };
    }

    static string ShinyName(Shiny s) => s switch
    {
        Shiny.Never => "never", Shiny.Random => "may be", Shiny.FixedValue => "fixed", _ => "always",
    };

    static JsonObject CardInfo(Game game, MysteryGift gift, string key = "")
    {
        var info = new JsonObject
        {
            ["key"] = key.Length > 0 ? key : GiftKey(gift),
            ["card_id"] = gift.CardID,
            ["text"] = gift is DataMysteryGift raw ? raw.Data[0x50] : 0,      // the card text index (CardEditor.cs)
            ["title"] = gift.CardTitle.Trim(),
            ["kind"] = gift.IsEntity ? (gift.IsEgg ? "egg" : "pokemon") : gift.IsItem ? "item" : gift switch
            {
                WC7 { IsBP: true } => "bp", WC7 { IsBean: true } => "beans", _ => "other",
            },
            ["games"] = new JsonArray(game.Versions.Where(v => Reaches(gift, v))
                .Select(v => (JsonNode)GameInfo.GetVersionName(v)).ToArray()),
        };
        if (gift is DataMysteryGift data && data is WC6 or WC7)
        {
            var (restrictVersion, restrictLanguage, date, multi) = data switch
            {
                WC6 w => (w.RestrictVersion, w.RestrictLanguage, w.Date, w.MultiObtain),
                WC7 w => (w.RestrictVersion, w.RestrictLanguage, w.Date, w.MultiObtain),
                _ => default,
            };
            info["restrict_version"] = restrictVersion;
            info["restrict_language"] = restrictLanguage;
            info["date"] = date?.ToString("yyyy-MM-dd") ?? "";
            info["multi_obtain"] = multi;
        }
        if (gift.IsEntity)
        {
            info["species"] = gift.Species;
            info["species_name"] = Name(Strings.Species, gift.Species);
            info["form"] = gift.Form;
            info["level"] = gift.Level;
            info["shiny"] = ShinyName(gift.Shiny);
            info["held_item"] = gift.HeldItem > 0 ? Name(Strings.Item, gift.HeldItem) : "";
            // A card with its own OT gives a Pokemon traded from that trainer; without one, the receiver is its OT.
            info["ot"] = gift.OriginalTrainerName;
            var language = gift switch { WC6 w => w.Language, WC7 w => w.Language, _ => 0 };
            info["language"] = language == 0 ? "" : ((LanguageID)language).ToString();
            info["moves"] = new JsonArray(new[] { gift.Moves.Move1, gift.Moves.Move2, gift.Moves.Move3, gift.Moves.Move4 }
                .Where(m => m != 0).Select(m => (JsonNode)Name(Strings.Move, m)).ToArray());
        }
        else if (gift.IsItem)
        {
            info["item"] = Name(Strings.Item, gift.ItemID);
            info["quantity"] = gift.Quantity;
        }
        return info;
    }

    // -- one card -----------------------------------------------------------------------------

    static MysteryGift CardFromBytes(Game game, byte[] data)
    {
        var restrictions = (byte)0;
        var language = (byte)0;
        if (data.Length == FullSize)
        {
            restrictions = data[0x000];
            language = data[0x1FF];
            data = data[FullCardAt..(FullCardAt + CardSize)];
        }
        if (data.Length != CardSize)
            throw new ArgumentException($"{data.Length} bytes is neither a card (264) nor a full distribution file (784)");
        MysteryGift gift = game.Generation == 6 ? new WC6(data) : game.Generation == 7 ? new WC7(data)
            : throw new ArgumentException("Mystery Gift cards are Gen 6 and 7 here");
        if (gift.IsEmpty)
            throw new ArgumentException("the card is empty");
        if (restrictions != 0 && gift is WC6 w6 && w6.RestrictVersion == 0)
            (w6.RestrictVersion, w6.RestrictLanguage) = (restrictions, language);
        if (restrictions != 0 && gift is WC7 w7 && w7.RestrictVersion == 0)
            (w7.RestrictVersion, w7.RestrictLanguage) = (restrictions, language);
        return gift;
    }

    static JsonObject Gift(Game game, JsonObject request)
    {
        MysteryGift gift;
        if ((string?)request["key"] is { } key)
            gift = Table(game).FirstOrDefault(g => g.IsEntity && GiftKey(g) == key && ForGame(game, g))
                   ?? throw new ArgumentException($"no card {key} for this game");
        else
            gift = CardFromBytes(game, Convert.FromBase64String((string)request["data"]!));
        var reply = GiftReply(game, gift, request, custom: false);
        if (request["key"] is not null)
            Undated(reply["card"]!.AsObject());
        return reply;
    }

    // PKHeX.Core stamps today's date into the cards of its database that carry none (355 of ORAS's 771 at
    // 26.10.18), so in its own cards today means "not known". A card from a file keeps the date it holds.
    static void Undated(JsonObject info)
    {
        if ((string?)info["date"] == DateTime.Today.ToString("yyyy-MM-dd"))
            info["date"] = "";
    }

    // The card made into its Pokemon for the trainer, on a version the card reaches, rolled again where a random
    // PID or IVs fail the check; the first legal copy is kept.
    static JsonObject GiftReply(Game game, MysteryGift gift, JsonObject request, bool custom)
    {
        var reply = new JsonObject { ["ok"] = true, ["card"] = CardInfo(game, gift), ["custom"] = custom };
        if (gift is DataMysteryGift data)
        {
            var bytes = data.Data.ToArray();
            reply["card_data"] = Convert.ToBase64String(bytes);
            reply["full_data"] = Convert.ToBase64String(FullFile(gift, bytes));
            reply["card_extension"] = game.Generation == 6 ? "wc6" : "wc7";
        }
        if (!gift.IsEntity)
        {
            reply["pokemon"] = null;           // an item, BP or beans: only Mystery Gift can deliver it
            return reply;
        }
        var trainer = Trainer(game, request);
        var versions = game.Versions.Where(v => Reaches(gift, v)).DefaultIfEmpty(game.Versions[0]);
        // A card with no date would give a Pokemon met on day zero, which no game makes: date a copy today, as the
        // day the save receives it.
        if (gift is WC6 { Date: null } or WC7 { Date: null })
        {
            gift = gift is WC6 c6 ? (MysteryGift)c6.Clone() : ((WC7)gift).Clone();
            if (gift is WC6 d6) d6.Date = DateOnly.FromDateTime(DateTime.Today);
            if (gift is WC7 d7) d7.Date = DateOnly.FromDateTime(DateTime.Today);
        }
        PKM? pk = null;
        LegalityAnalysis? la = null;
        foreach (var version in versions)
        {
            var receiver = new SimpleTrainerInfo(version)
            {
                OT = trainer.OT, TID16 = trainer.TID16, SID16 = trainer.SID16, Language = trainer.Language, Gender = trainer.Gender,
            };
            for (var roll = 0; roll < 8; roll++)
            {
                pk = gift.ConvertToPKM(receiver, EncounterCriteria.Unrestricted);
                pk.RefreshChecksum();
                la = new LegalityAnalysis(pk);
                if (!la.Valid && pk.IsEgg && pk.MetDate is not null)
                {
                    // PKHeX gives some cards' unhatched eggs a met date (card 142's Mega Evolution eggs, dated 2016-04-01)
                    // that its own check then refuses; an egg not yet hatched has none.
                    pk.MetDate = null;
                    pk.RefreshChecksum();
                    la = new LegalityAnalysis(pk);
                }
                if (la.Valid)
                    break;
            }
            if (la!.Valid)
                break;
        }
        pk!.ResetPartyStats();                  // the stats the game will show, from level, nature, IVs and EVs
        reply["pokemon"] = Describe(game, pk, la!, null);
        return reply;
    }

    static byte[] FullFile(MysteryGift gift, byte[] card)
    {
        var full = new byte[FullSize];
        card.CopyTo(full, FullCardAt);
        var (version, language) = gift switch
        {
            WC6 w => (w.RestrictVersion, w.RestrictLanguage),
            WC7 w => (w.RestrictVersion, w.RestrictLanguage),
            _ => ((byte)0, (byte)0),
        };
        full[0x000] = version == 0 ? (byte)15 : version;
        full[0x1FF] = language;
        return full;
    }

    // -- a custom card: CardEditor.cs ------------------------------------------------------------

    // What the species knows at that level from levelling up, as a wild one of that level would.
    static ushort[] GetLevelUpMoves(Game game, ushort species, byte level)
    {
        var source = GameData.GetLearnSource(game.Versions[0]);
        var learnset = (Learnset)source.GetType().GetMethod("GetLearnset", [typeof(ushort), typeof(byte)])!
            .Invoke(source, [species, (byte)0])!;
        var moves = new ushort[4];
        learnset.SetEncounterMoves(level, moves);
        return moves.Where(m => m != 0).ToArray();
    }
}

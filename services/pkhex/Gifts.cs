// Mystery Gift cards for Gen 6 and 7 (docs/mystery_gift.md): the official cards PKHeX.Core embeds, a card opened
// from a file, and custom cards built from a form. Every card can be made into the Pokemon it gives, as the
// requested trainer would receive it, and checked; a custom card's Pokemon matches no real event, so PKHeX's
// check refuses it ("custom" says so).
//
// Card files: a .wc6 / .wc7 is the 264-byte card; a full file (.wc6full / .wc7full) is 784 bytes, the 520-byte
// distribution header a local distributor sends, then the card at 0x208 (docs/mystery_gift.md, "The full file"):
//   0x000      the games that may receive it (RestrictVersion bits; 0x001-0x003 zero)
//   0x004      the distribution text, UTF-16 with \n line breaks, NUL-terminated (0x1FA bytes)
//   0x1FE      1: the receiving screen's halo effect
//   0x1FF      the language it is limited to (0: any)
//   0x200      1: receivable once a day
//   0x201      the card's place in a set; 0x204 the number of cards in the set
//   0x202      CRC-16/CCITT-FALSE of all 784 bytes with this field zero, little-endian (every one of PKHeX's
//              422 Gen 6 and 801 Gen 7 full files matches)
//   0x205      Gen 6: 0x46; Gen 7: a weight for random sets, mostly 0
//   0x206      0x46 0x46

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    const int CardSize = 0x108, FullSize = 0x310, FullCardAt = 0x208;
    const int FullTextAt = 0x004, FullTextSize = 0x1FA, FullHalo = 0x1FE, FullLanguage = 0x1FF, FullOncePerDay = 0x200,
              FullChecksumAt = 0x202;

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

    static MysteryGift CardFromBytes(Game game, byte[] data) => CardFromBytes(game, data, out _);

    // ``header``: a full file's distribution header (its first 0x208 bytes), or null for a bare card.
    static MysteryGift CardFromBytes(Game game, byte[] data, out byte[]? header)
    {
        header = data.Length == FullSize ? data[..FullCardAt] : null;
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
        byte[]? header = null;
        if ((string?)request["key"] is { } key)
            gift = Table(game).FirstOrDefault(g => g.IsEntity && GiftKey(g) == key && ForGame(game, g))
                   ?? throw new ArgumentException($"no card {key} for this game");
        else
            gift = CardFromBytes(game, Convert.FromBase64String((string)request["data"]!), out header);
        var reply = GiftReply(game, gift, request, custom: false, header);
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
    static JsonObject GiftReply(Game game, MysteryGift gift, JsonObject request, bool custom, byte[]? header = null)
    {
        var reply = new JsonObject { ["ok"] = true, ["card"] = CardInfo(game, gift), ["custom"] = custom };
        if (gift is DataMysteryGift data)
        {
            var bytes = data.Data.ToArray();
            reply["card_data"] = Convert.ToBase64String(bytes);
            reply["full_data"] = Convert.ToBase64String(FullFile(game, gift, bytes, request, header));
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

    // The full file a local distributor sends for the card: the header of the full file the card came from, else
    // that of PKHeX's own copy of an official one (its real distribution text and set fields), else a new header;
    // then the card as given (an official file of an undated card holds it undated). Request fields
    // distribution_text and halo change the header; the text defaults to what the card gives. The checksum is
    // always computed anew, so an official card the same byte for byte gives back PKHeX's file exactly.
    static byte[] FullFile(Game game, MysteryGift gift, byte[] card, JsonObject request, byte[]? header)
    {
        var text = (string?)request["distribution_text"] ?? "";
        var halo = request["halo"] is JsonValue h && h.TryGetValue(out bool on) ? on : (bool?)null;
        var full = new byte[FullSize];
        var (version, language) = gift switch
        {
            WC6 w => (w.RestrictVersion, w.RestrictLanguage),
            WC7 w => (w.RestrictVersion, w.RestrictLanguage),
            _ => ((byte)0, (byte)0),
        };
        var source = header ?? OfficialFullFile(game.Generation, card);
        if (source is not null)
            source.AsSpan(0, FullCardAt).CopyTo(full);
        else
        {
            full[0x205] = game.Generation == 6 ? (byte)0x46 : (byte)0;
            full[0x206] = full[0x207] = 0x46;
            full[FullOncePerDay] = (byte)(gift is WC7 { GiftOncePerDay: true } ? 1 : 0);
            SetDistributionText(full, DefaultDistributionText(gift));
        }
        card.CopyTo(full, FullCardAt);
        // A bare card holds no restrictions (they live in the header): a header taken from a file keeps its own.
        if (source is null || version != 0)
        {
            full[0x000] = version == 0 ? (byte)15 : version;
            full[FullLanguage] = language;
        }
        if (text.Length > 0)
            SetDistributionText(full, text);
        if (halo is { } shown)
            full[FullHalo] = (byte)(shown ? 1 : 0);
        RefreshFullChecksum(full);
        return full;
    }

    static void SetDistributionText(byte[] full, string text)
    {
        var bytes = System.Text.Encoding.Unicode.GetBytes(text.Replace("\r\n", "\n"));
        if (bytes.Length > FullTextSize - 2)
            throw new ArgumentException($"the distribution text is at most {(FullTextSize - 2) / 2} characters");
        Array.Clear(full, FullTextAt, FullTextSize);
        bytes.CopyTo(full, FullTextAt);
    }

    static string GetDistributionText(byte[] full)
    {
        var text = System.Text.Encoding.Unicode.GetString(full, FullTextAt, FullTextSize);
        var end = text.IndexOf('\0');
        return end >= 0 ? text[..end] : text;
    }

    // How official cards' distribution text puts it, for a card that brings none.
    static string DefaultDistributionText(MysteryGift gift) =>
        gift.IsEgg ? "A Pokémon Egg\nwill be given to you as a gift!"
        : gift.IsEntity ? $"{Name(Strings.Species, gift.Species)}\nwill be given to you as a gift!"
        : gift.IsItem ? $"{Name(Strings.Item, gift.ItemID)}\nwill be given to you as a gift!"
        : gift.CardTitle.Trim();

    static void RefreshFullChecksum(byte[] full)
    {
        full[FullChecksumAt] = full[FullChecksumAt + 1] = 0;
        ushort crc = 0xFFFF;                                   // CRC-16/CCITT-FALSE, as gflnet's
        foreach (var b in full)
        {
            crc ^= (ushort)(b << 8);
            for (var i = 0; i < 8; i++)
                crc = (crc & 0x8000) != 0 ? (ushort)((crc << 1) ^ 0x1021) : (ushort)(crc << 1);
        }
        full[FullChecksumAt] = (byte)crc;
        full[FullChecksumAt + 1] = (byte)(crc >> 8);
    }

    static bool FullChecksumValid(byte[] full)
    {
        var copy = full.ToArray();
        RefreshFullChecksum(copy);
        return copy[FullChecksumAt] == full[FullChecksumAt] && copy[FullChecksumAt + 1] == full[FullChecksumAt + 1];
    }

    // The official full files PKHeX.Core embeds (wc6full.pkl, wc7full.pkl), by the card they carry. PKHeX stamps
    // today's date into the cards of its database that carry none, so a card is also looked up with its date
    // (0x4C, four bytes) zero, among the full files whose card has no date.
    static readonly Dictionary<int, Dictionary<string, byte[]>> OfficialFull = [];
    const int CardDateAt = 0x4C;

    static string UndatedKey(ReadOnlySpan<byte> card)
    {
        var copy = card.ToArray();
        Array.Clear(copy, CardDateAt, 4);
        return "undated:" + Convert.ToHexString(copy);
    }

    static byte[]? OfficialFullFile(int generation, byte[] card)
    {
        lock (OfficialFull)
        {
            if (!OfficialFull.TryGetValue(generation, out var files))
            {
                files = [];
                var raw = Util.GetBinaryResource(generation == 6 ? "wc6full.pkl" : "wc7full.pkl");
                for (var at = 0; at + FullSize <= raw.Length; at += FullSize)
                {
                    var full = raw[at..(at + FullSize)];
                    var inner = full.AsSpan(FullCardAt, CardSize);
                    files.TryAdd(Convert.ToHexString(inner), full);
                    if (inner.Slice(CardDateAt, 4).IndexOfAnyExcept((byte)0) < 0)
                        files.TryAdd(UndatedKey(inner), full);
                }
                OfficialFull[generation] = files;
            }
            return files.GetValueOrDefault(Convert.ToHexString(card)) ?? files.GetValueOrDefault(UndatedKey(card));
        }
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

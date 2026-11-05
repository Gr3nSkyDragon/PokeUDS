// The card editor's schema: every field a Gen 6/7 Wonder Card holds, as JSON, both ways (docs/wonder_cards.md).
//
//   card        {fields} -> the card (and the full file) built from them, and what it gives
//   card_fields {data | key} -> any card as {fields}, so an official card or a file can be opened and changed
//
// A card built from card_fields' output is the same card byte for byte (scripts/check_gifts.py checks all of
// PKHeX's). Stats are not stored in a card: the game computes them from species, level, nature, IVs and EVs.
// Six-stat lists (ivs, evs) are in display order: HP, Attack, Defense, Sp. Atk, Sp. Def, Speed.
//
// An egg is kind "pokemon" with is_egg; kind "egg" is that with official event eggs' defaults (level 1, from
// "a Pokemon Center", met nowhere yet).
// Card:     card_id, title (36 characters; the field holds 37), text (the card text index at 0x50: a message of the game's own),
//           date (yyyy-mm-dd), kind ("pokemon", "item"; Gen 7 also "bp", "beans"), versions (RestrictVersion
//           bits: 1 X/Sun, 2 Y/Moon, 4 AS/US, 8 OR/UM; 0 all), restrict_language (full file only),
//           multi_obtain, once_per_day (Gen 7), repeatable (Gen 7), card_flags (raw byte 0x52), extra_66.
// Pokemon:  species, form, level, met_level, is_egg, egg_location, met_location, ball, held_item, moves[4],
//           relearn[4], nickname, language (0: the receiver's), ot (""/absent: the receiver; then tid, sid,
//           ot_gender), origin_game (0: the receiver's), shiny ("random", "always", "never", "fixed" with pid),
//           encryption_constant (0: random), nature (-1 random), gender (-1 random, 0 male, 1 female, 2 none),
//           ability ("1", "2", "hidden", "random12", "random12h"), ivs[6] (-1 random), perfect_ivs (1-3, with
//           random ivs), evs[6], contest[6] (cool, beauty, cute, smart, tough, sheen), ribbons[names],
//           memory {intensity, memory, variable, feeling}.
// Items:    items [{item, quantity}] (Gen 6 one, Gen 7 up to six); bp: quantity; beans: bean, quantity.

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    static readonly string[] RibbonNames =
    [
        "champion_battle", "champion_regional", "champion_national", "country", "national", "earth", "world", "event",
        "champion_world", "birthday", "special", "souvenir", "wishing", "classic", "premier",
    ];

    static bool GetRibbon(dynamic c, string name) => name switch
    {
        "champion_battle" => c.RibbonChampionBattle, "champion_regional" => c.RibbonChampionRegional,
        "champion_national" => c.RibbonChampionNational, "country" => c.RibbonCountry, "national" => c.RibbonNational,
        "earth" => c.RibbonEarth, "world" => c.RibbonWorld, "event" => c.RibbonEvent, "champion_world" => c.RibbonChampionWorld,
        "birthday" => c.RibbonBirthday, "special" => c.RibbonSpecial, "souvenir" => c.RibbonSouvenir,
        "wishing" => c.RibbonWishing, "classic" => c.RibbonClassic, "premier" => c.RibbonPremier, _ => false,
    };

    static void SetRibbon(dynamic c, string name, bool on)
    {
        switch (name)
        {
            case "champion_battle": c.RibbonChampionBattle = on; break;
            case "champion_regional": c.RibbonChampionRegional = on; break;
            case "champion_national": c.RibbonChampionNational = on; break;
            case "country": c.RibbonCountry = on; break;
            case "national": c.RibbonNational = on; break;
            case "earth": c.RibbonEarth = on; break;
            case "world": c.RibbonWorld = on; break;
            case "event": c.RibbonEvent = on; break;
            case "champion_world": c.RibbonChampionWorld = on; break;
            case "birthday": c.RibbonBirthday = on; break;
            case "special": c.RibbonSpecial = on; break;
            case "souvenir": c.RibbonSouvenir = on; break;
            case "wishing": c.RibbonWishing = on; break;
            case "classic": c.RibbonClassic = on; break;
            case "premier": c.RibbonPremier = on; break;
            default: throw new ArgumentException($"no ribbon {name}");
        }
    }

    // Card IVs are stored HP, Atk, Def, Spe, SpA, SpD; the schema uses the display order HP, Atk, Def, SpA, SpD, Spe.
    static readonly int[] DisplayToCard = [0, 1, 2, 4, 5, 3];

    static int[] SixFrom(JsonNode? node, int missing) =>
        Enumerable.Range(0, 6).Select(i => node is JsonArray a && i < a.Count && a[i] is { } v ? (int)v : missing).ToArray();

    // -- fields -> card -----------------------------------------------------------------------

    static JsonObject BuildCard(Game game, JsonObject r)
    {
        if (game.Generation is not (6 or 7))
            throw new ArgumentException("custom cards are Gen 6 and 7");
        var card = MakeCard(game, r);
        return GiftReply(game, card, r, custom: true);
    }

    static DataMysteryGift MakeCard(Game game, JsonObject r)
    {
        if ((string?)r["kind"] == "egg")
        {
            // An egg is a Pokemon card with the egg flag, set as official event eggs are: level 1, from "a Pokemon
            // Center", no met place until it hatches (one there fails PKHeX's met-date check).
            r = (JsonObject)r.DeepClone();
            r["kind"] = "pokemon";
            r["is_egg"] = true;
            r["level"] ??= 1;
            r["met_level"] ??= 1;
            r["met_location"] ??= 0;
            r["egg_location"] ??= 40010;
        }
        var six = game.Generation == 6;
        DataMysteryGift card = six ? new WC6(new byte[CardSize]) : new WC7(new byte[CardSize]);
        dynamic c = card;
        var kind = (string?)r["kind"] ?? "pokemon";
        c.CardID = Math.Clamp((int?)r["card_id"] ?? 2000, 0, 0xFFFF);
        c.CardTitle = (string?)r["title"] ?? "PokeUDS gift";
        // The field holds 37 characters with no terminator; real cards use at most 36 (the app's limit).
        if (((string?)r["title"] ?? "").Length > 37)
            throw new ArgumentException("a card title is at most 36 characters");
        card.Data[0x50] = (byte)((int?)r["text"] ?? (six ? 43 : 44));          // the most used official card text
        c.Date = (string?)r["date"] is { Length: > 0 } d ? DateOnly.Parse(d) : DateOnly.FromDateTime(DateTime.Today);
        c.CardType = kind switch
        {
            "pokemon" => 0, "item" => 1,
            "beans" when !six => 2, "bp" when !six => 3,                     // Gen 7: 2 Poke Beans, 3 Battle Points
            _ => throw new ArgumentException(six ? $"Gen 6 cards give a Pokemon or an item, not {kind}" : $"no card kind {kind}"),
        };
        c.RestrictVersion = (byte)((int?)r["versions"] ?? 0);
        c.RestrictLanguage = (byte)((int?)r["restrict_language"] ?? 0);
        // 0x53: whether the card can be received again; Gen 7 cards also hold 2 there, so a number is kept as is.
        card.Data[0x53] = r["multi_obtain"] switch
        {
            null => 0,
            JsonValue v when v.TryGetValue(out bool b) => (byte)(b ? 1 : 0),
            var v => (byte)(int)v!,
        };
        if (card is WC7 w7)
        {
            if (r["repeatable"] is not null) w7.GiftRepeatable = (bool)r["repeatable"]!;
            if (r["once_per_day"] is not null) w7.GiftOncePerDay = (bool)r["once_per_day"]!;
        }
        if ((int?)r["card_flags"] is { } flags)
            card.Data[0x52] = (byte)flags;
        card.Data[0x66] = (byte)((int?)r["extra_66"] ?? 0xFF);                // 0xFF on every distributed card
        card.Data[0x64] = (byte)((int?)r["extra_64"] ?? 0);                   // 1 only on PKHeX's own demo stand-ins
        switch (kind)
        {
            case "pokemon": FillPokemon(game, card, r); break;
            case "item": FillItems(game, card, r); break;
            case "bp": ((WC7)card).BP = Math.Clamp((int?)r["quantity"] ?? 1, 1, 9999); break;
            case "beans":
                ((WC7)card).Bean = (int?)r["bean"] ?? 0;
                ((WC7)card).Quantity = Math.Clamp((int?)r["quantity"] ?? 1, 0, 999);
                break;
        }
        return card;
    }

    static void FillItems(Game game, DataMysteryGift card, JsonObject r)
    {
        var items = (r["items"]?.AsArray() ?? []).Select(n => ((int?)n?["item"] ?? 0, (int?)n?["quantity"] ?? 1)).ToList();
        if (items.Count == 0 && (int?)r["item"] is { } single)                 // the short form: item, quantity
            items.Add((single, (int?)r["quantity"] ?? 1));
        if (items.Count == 0 || items.Any(i => i.Item1 <= 0))
            throw new ArgumentException("an item card needs an item");
        if (card is WC6 w6)
        {
            if (items.Count > 1)
                throw new ArgumentException("a Gen 6 card holds one item");
            w6.ItemID = items[0].Item1;
            w6.Quantity = Math.Clamp(items[0].Item2, 0, 999);
            return;
        }
        var w7 = (WC7)card;
        if (items.Count > 6)
            throw new ArgumentException("a Gen 7 card holds up to six items");
        for (var i = 0; i < items.Count; i++)
        {
            w7.SetItem(i, (ushort)items[i].Item1);
            w7.SetQuantity(i, (ushort)Math.Clamp(items[i].Item2, 0, 999));
        }
    }

    static void FillPokemon(Game game, DataMysteryGift card, JsonObject r)
    {
        var species = (ushort)((int?)r["species"] ?? throw new ArgumentException("a Pokemon card needs a species"));
        if (species == 0 || species > game.Save().MaxSpeciesID)
            throw new ArgumentException($"species {species} is not in this game");
        dynamic c = card;
        c.Species = species;
        c.Form = (byte)((int?)r["form"] ?? 0);
        c.Level = (byte)Math.Clamp((int?)r["level"] ?? 10, 1, 100);
        if (card is WC7 extra)
            extra.AdditionalItem = (ushort)((int?)r["additional_item"] ?? 0);  // a second item handed over with the Pokemon
        var metLevel = (byte)Math.Clamp((int?)r["met_level"] ?? (int)c.Level, 0, 100);
        if (card is WC7 w7) w7.MetLevel = metLevel; else card.Data[0xA8] = metLevel;      // Gen 6: not named by PKHeX
        c.IsEgg = (bool?)r["is_egg"] ?? false;
        c.EggLocation = (ushort)((int?)r["egg_location"] ?? 0);
        c.Location = (ushort)((int?)r["met_location"] ?? 40001);              // "a lovely place", as most event cards
        c.Ball = (byte)((int?)r["ball"] ?? 4);
        c.HeldItem = (int?)r["held_item"] ?? 0;
        var moves = SixFrom(r["moves"], 0).Take(4).Select(m => (ushort)m).ToArray();
        if (r["moves"] is null || moves.All(m => m == 0))
            moves = GetLevelUpMoves(game, species, (byte)c.Level).Concat(new ushort[4]).Take(4).ToArray();
        (c.Move1, c.Move2, c.Move3, c.Move4) = (moves[0], moves[1], moves[2], moves[3]);
        var relearn = SixFrom(r["relearn"], 0).Take(4).Select(m => (ushort)m).ToArray();
        (c.RelearnMove1, c.RelearnMove2, c.RelearnMove3, c.RelearnMove4) = (relearn[0], relearn[1], relearn[2], relearn[3]);
        if ((string?)r["nickname"] is { Length: > 0 } nick)
        {
            if (nick.Length > 12)
                throw new ArgumentException("a nickname is at most 12 characters");
            c.Nickname = nick;
        }
        c.Language = (int?)r["language"] ?? 0;
        var ot = (string?)r["ot"] ?? "";
        if (ot.Length > 0)
        {
            if (ot.Length > 12)
                throw new ArgumentException("an OT name is at most 12 characters");
            c.OriginalTrainerName = ot;
            c.TID16 = (ushort)((int?)r["tid"] ?? 12345);
            c.SID16 = (ushort)((int?)r["sid"] ?? 54321);
            c.OTGender = (byte)((int?)r["ot_gender"] ?? 0);
        }
        else if (r["tid"] is not null || r["ot_gender"] is not null)
        {
            // An opened card: what it holds, as it holds it (some "receiver is the OT" cards carry IDs too).
            c.TID16 = (ushort)((int?)r["tid"] ?? 0);
            c.SID16 = (ushort)((int?)r["sid"] ?? 0);
            c.OTGender = (byte)((int?)r["ot_gender"] ?? 3);
        }
        else if (game.Generation == 7)
        {
            c.OTGender = (byte)3;              // the cards' "receiver is the OT": name, IDs and gender come from the save
        }
        else if (r["trainer"] is not null && !(bool)c.IsEgg)
        {
            // Gen 6 has no such marker for a Pokemon (every real non-egg card names its OT, and the game copies the
            // card's IDs), so a card for the trainer names the trainer.
            var tr = Trainer(game, r);
            (c.OriginalTrainerName, c.TID16, c.SID16, c.OTGender) = (tr.OT, tr.TID16, tr.SID16, tr.Gender);
        }
        else
        {
            c.OTGender = (byte)3;
        }
        c.OriginGame = (byte)((int?)r["origin_game"] ?? 0);
        c.PIDType = ((string?)r["shiny"] ?? "random") switch
        {
            "always" => ShinyType6.Always, "never" => ShinyType6.Never, "fixed" => ShinyType6.FixedValue, _ => ShinyType6.Random,
        };
        c.PID = (uint)((long?)r["pid"] ?? 0);
        c.EncryptionConstant = (uint)((long?)r["encryption_constant"] ?? 0);
        card.Data[0xA0] = (byte)((int?)r["nature"] is { } n and >= 0 and < 25 ? n : 0xFF);    // 0xFF: random, as cards store it
        c.Gender = (byte)((int?)r["gender"] is { } g and >= 0 and <= 2 ? g : 3);
        c.AbilityType = ((string?)r["ability"] ?? "random12") switch
        {
            "1" => 0, "2" => 1, "hidden" => 2, "random12h" => 4, _ => 3,
        };
        var ivs = SixFrom(r["ivs"], -1);
        var perfect = Math.Clamp((int?)r["perfect_ivs"] ?? 0, 0, 3);
        var cardIVs = new int[6];
        for (var i = 0; i < 6; i++)
            cardIVs[DisplayToCard[i]] = ivs[i] is >= 0 and <= 31 ? ivs[i] : 0xFF;
        if (perfect > 0)
            cardIVs[0] = 0xFB + perfect;       // 0xFC, 0xFD, 0xFE in the HP byte: 1, 2 or 3 perfect IVs, the rest random
        (c.IV_HP, c.IV_ATK, c.IV_DEF, c.IV_SPE, c.IV_SPA, c.IV_SPD) = (cardIVs[0], cardIVs[1], cardIVs[2], cardIVs[3], cardIVs[4], cardIVs[5]);
        var evs = SixFrom(r["evs"], 0).Select(v => Math.Clamp(v, 0, 252)).ToArray();
        if (evs.Sum() > 510)
            throw new ArgumentException("EVs total at most 510");
        (c.EV_HP, c.EV_ATK, c.EV_DEF, c.EV_SPA, c.EV_SPD, c.EV_SPE) = (evs[0], evs[1], evs[2], evs[3], evs[4], evs[5]);
        var contest = SixFrom(r["contest"], 0).Select(v => (byte)Math.Clamp(v, 0, 255)).ToArray();
        (c.ContestCool, c.ContestBeauty, c.ContestCute, c.ContestSmart, c.ContestTough, c.ContestSheen) =
            (contest[0], contest[1], contest[2], contest[3], contest[4], contest[5]);
        var ribbons = r["ribbons"] is JsonArray list ? list.Select(x => (string)x!).ToHashSet()
                    : (string?)r["ribbon"] is { Length: > 0 } one ? [one] : ["classic"];
        foreach (var name in ribbons)
            if (!RibbonNames.Contains(name))
                throw new ArgumentException($"no ribbon {name} (one of {string.Join(", ", RibbonNames)})");
        foreach (var name in RibbonNames)
            SetRibbon(c, name, ribbons.Contains(name));
        if (r["memory"] is JsonObject m)
        {
            c.OriginalTrainerMemoryIntensity = (byte)((int?)m["intensity"] ?? 0);
            c.OriginalTrainerMemory = (byte)((int?)m["memory"] ?? 0);
            c.OriginalTrainerMemoryVariable = (ushort)((int?)m["variable"] ?? 0);
            c.OriginalTrainerMemoryFeeling = (byte)((int?)m["feeling"] ?? 0);
        }
    }

    // -- card -> fields -----------------------------------------------------------------------

    static JsonObject CardFieldsReply(Game game, JsonObject request)
    {
        MysteryGift gift;
        if ((string?)request["key"] is { } key)
            gift = Table(game).FirstOrDefault(g => GiftKey(g) == key && ForGame(game, g))
                   ?? throw new ArgumentException($"no card {key} for this game");
        else
            gift = CardFromBytes(game, Convert.FromBase64String((string)request["data"]!));
        return new JsonObject { ["ok"] = true, ["fields"] = CardFields(game, (DataMysteryGift)gift) };
    }

    static JsonObject CardFields(Game game, DataMysteryGift card)
    {
        dynamic c = card;
        var six = card is WC6;
        var kind = (int)c.CardType switch { 0 => "pokemon", 1 => "item", 2 when !six => "beans", 3 when !six => "bp", _ => "other" };
        var f = new JsonObject
        {
            ["card_id"] = (int)c.CardID,
            ["title"] = (string)c.CardTitle,
            ["text"] = card.Data[0x50],
            ["date"] = ((DateOnly?)c.Date)?.ToString("yyyy-MM-dd") ?? "",
            ["kind"] = kind,
            ["versions"] = (int)c.RestrictVersion,
            ["restrict_language"] = (int)c.RestrictLanguage,
            ["multi_obtain"] = card.Data[0x53] is 0 or 1 ? card.Data[0x53] == 1 : card.Data[0x53],
            ["card_flags"] = card.Data[0x52],
            ["extra_66"] = card.Data[0x66],
            ["extra_64"] = card.Data[0x64],
        };
        if (card is WC7 w7)
        {
            f["repeatable"] = w7.GiftRepeatable;
            f["once_per_day"] = w7.GiftOncePerDay;
        }
        switch (kind)
        {
            case "item":
                var items = new JsonArray();
                if (card is WC6 w6)
                    items.Add(new JsonObject { ["item"] = w6.ItemID, ["quantity"] = w6.Quantity });
                else
                    for (var i = 0; i < 6 && ((WC7)card).GetItem(i) != 0; i++)
                        items.Add(new JsonObject { ["item"] = (int)((WC7)card).GetItem(i), ["quantity"] = (int)((WC7)card).GetQuantity(i) });
                f["items"] = items;
                break;
            case "bp":
                f["quantity"] = ((WC7)card).BP;
                break;
            case "beans":
                f["bean"] = ((WC7)card).Bean;
                f["quantity"] = ((WC7)card).Quantity;
                break;
            case "pokemon":
                f["species"] = (int)c.Species;
                f["form"] = (int)c.Form;
                f["level"] = (int)c.Level;
                f["met_level"] = card is WC7 m7 ? m7.MetLevel : card.Data[0xA8];
                if (card is WC7 a7)
                    f["additional_item"] = (int)a7.AdditionalItem;
                f["is_egg"] = (bool)c.IsEgg;
                f["egg_location"] = (int)c.EggLocation;
                f["met_location"] = (int)c.Location;
                f["ball"] = (int)c.Ball;
                f["held_item"] = (int)c.HeldItem;
                f["moves"] = new JsonArray((int)c.Move1, (int)c.Move2, (int)c.Move3, (int)c.Move4);
                f["relearn"] = new JsonArray((int)c.RelearnMove1, (int)c.RelearnMove2, (int)c.RelearnMove3, (int)c.RelearnMove4);
                f["nickname"] = (string)c.Nickname;
                f["language"] = (int)c.Language;
                var ot = (string)c.OriginalTrainerName;
                f["ot"] = ot;
                f["tid"] = (int)c.TID16;
                f["sid"] = (int)c.SID16;
                f["ot_gender"] = (int)c.OTGender;
                f["origin_game"] = (int)c.OriginGame;
                f["shiny"] = (ShinyType6)c.PIDType switch
                {
                    ShinyType6.Always => "always", ShinyType6.Never => "never", ShinyType6.FixedValue => "fixed", _ => "random",
                };
                f["pid"] = (long)(uint)c.PID;
                f["encryption_constant"] = (long)(uint)c.EncryptionConstant;
                f["nature"] = card.Data[0xA0] < 25 ? card.Data[0xA0] : -1;
                f["gender"] = (int)c.Gender is var gender and <= 2 ? gender : -1;
                f["ability"] = (int)c.AbilityType switch { 0 => "1", 1 => "2", 2 => "hidden", 4 => "random12h", _ => "random12" };
                int[] raw = [(int)c.IV_HP, (int)c.IV_ATK, (int)c.IV_DEF, (int)c.IV_SPE, (int)c.IV_SPA, (int)c.IV_SPD];
                f["perfect_ivs"] = raw[0] is >= 0xFC and <= 0xFE ? raw[0] - 0xFB : 0;
                f["ivs"] = new JsonArray(DisplayToCard.Select(i => (JsonNode)(raw[i] <= 31 ? raw[i] : -1)).ToArray());
                f["evs"] = new JsonArray((int)c.EV_HP, (int)c.EV_ATK, (int)c.EV_DEF, (int)c.EV_SPA, (int)c.EV_SPD, (int)c.EV_SPE);
                f["contest"] = new JsonArray((int)c.ContestCool, (int)c.ContestBeauty, (int)c.ContestCute, (int)c.ContestSmart,
                                             (int)c.ContestTough, (int)c.ContestSheen);
                f["ribbons"] = new JsonArray(RibbonNames.Where(n => GetRibbon(c, n)).Select(n => (JsonNode)n).ToArray());
                f["memory"] = new JsonObject
                {
                    ["intensity"] = (int)c.OriginalTrainerMemoryIntensity, ["memory"] = (int)c.OriginalTrainerMemory,
                    ["variable"] = (int)c.OriginalTrainerMemoryVariable, ["feeling"] = (int)c.OriginalTrainerMemoryFeeling,
                };
                break;
        }
        return f;
    }
}

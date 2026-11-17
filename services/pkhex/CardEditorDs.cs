// The card editor's schema for the DS games' cards (CardEditor.cs has Gen 6/7's; docs/wonder_cards.md): Gen 5's
// .pgf and Gen 4's .pcd, both ways. Each card gives a Pokemon or an item; Gen 5 also an egg.
//
// Gen 5 (PGF) holds the same kind of fields as a Gen 6/7 card, fewer of them: no EVs, relearn moves, memories,
// encryption constant or perfect-IV count; one item; no fixed PID (PKHeX's database has none either).
//   versions: RestrictVersion bits, 1 White, 2 Black, 4 White 2, 8 Black 2 (0 all four).
//   ot "" with ot_gender 3 makes the receiver the OT, as on official cards.
//   distribution_text is the .bin's (Gifts.cs WifiFile); a .pgf holds no card text.
// Gen 4 (PCD) is a PGT gift (a whole PK4 for a Pokemon) inside a card. PokeUDS writes onto one of PKHeX's official
// cards of the same kind, so the bytes nobody has named (0x152, 0x348, 0x354 and the PK4's own defaults: fateful
// encounter, Cherish Ball, met nowhere until received) are a real card's:
//   versions: the card's compatibility bits by game id, 0x80 HeartGold, 0x100 SoulSilver, 0x400 Diamond, 0x800 Pearl,
//             0x1000 Platinum (0 all five); written at 0x14C.
//   distribution_text: the card's own text (0x154, 0x182 bytes in the game's encoding), the message on the card.
//   shiny: "never" (PID 1, as every official random card: the game rolls the PID, and PKHeX's 247 Gen 4 Pokemon
//          cards show such a gift is never shiny; "random", an older name, is the same), "always" (PokeUDS picks a PID
//          shiny for the card's OT, as the 45 official shiny Gen 4 events are fixed-PID cards; nature, gender and
//          ability can be chosen, as Gen 4 derives them from the PID), or "fixed" with pid.
//   IVs are rolled by the game (no official card sets them), and every official card names its OT, so a card "for
//   whoever receives it" names the trainer from the request, as Gen 6 does.
//   icons: the three species the card shows at 0x34A/0x34C/0x34E; a Pokemon card shows its own in the middle.

using System.Text.Json.Nodes;
using PKHeX.Core;

static partial class Program
{
    const int PgfLevelAt = 0x5B, PcdCompatibilityAt = 0x14C, PcdTextAt = 0x154, PcdTextSize = 0x182, PcdIconsAt = 0x34A;
    const ushort Gen4AllGames = 0x1D80;            // HeartGold, SoulSilver, Diamond, Pearl, Platinum
    const int GiftPokemon4 = 1, GiftItem4 = 3;     // GiftType4

    static DataMysteryGift MakeDsCard(Game game, JsonObject r)
    {
        var kind = (string?)r["kind"] ?? "pokemon";
        if (kind == "egg")
        {
            if (game.Generation == 4)
                throw new ArgumentException("Gen 4 egg cards are not supported: no official one to build on");
            r = (JsonObject)r.DeepClone();
            r["kind"] = kind = "pokemon";
            r["is_egg"] = true;
            r["level"] ??= 1;
            r["met_level"] ??= 1;
        }
        if (kind is not ("pokemon" or "item"))
            throw new ArgumentException($"Gen {game.Generation} cards give a Pokemon or an item here, not {kind}");
        var title = (string?)r["title"] ?? "PokeUDS gift";
        if (title.Length > 36)
            throw new ArgumentException("a card title is at most 36 characters");
        return game.Generation == 4 ? MakePcd(game, r, kind, title) : MakePgf(game, r, kind, title);
    }

    static (int Item, int Quantity) OneItem(JsonObject r, int generation)
    {
        var items = (r["items"]?.AsArray() ?? []).Select(n => ((int?)n?["item"] ?? 0, (int?)n?["quantity"] ?? 1)).ToList();
        if (items.Count == 0 && (int?)r["item"] is { } single)
            items.Add((single, 1));
        if (items.Count == 0 || items[0].Item1 <= 0)
            throw new ArgumentException("an item card needs an item");
        if (items.Count > 1)
            throw new ArgumentException($"a Gen {generation} card holds one item");
        return items[0];
    }

    static ushort DsSpecies(Game game, JsonObject r)
    {
        var species = (ushort)((int?)r["species"] ?? throw new ArgumentException("a Pokemon card needs a species"));
        if (species == 0 || species > game.Save().MaxSpeciesID)
            throw new ArgumentException($"species {species} is not in this game");
        return species;
    }

    static ushort[] DsMoves(Game game, JsonObject r, ushort species, byte level)
    {
        var moves = SixFrom(r["moves"], 0).Take(4).Select(m => (ushort)m).ToArray();
        if (r["moves"] is null || moves.All(m => m == 0))
            moves = GetLevelUpMoves(game, species, level).Concat(new ushort[4]).Take(4).ToArray();
        return moves;
    }

    // Gen 4 and 5 names: an OT is at most 7 characters, a nickname 10.
    static string DsName(JsonObject r, string key, int limit, string what)
    {
        var name = (string?)r[key] ?? "";
        if (name.Length > limit)
            throw new ArgumentException($"{what} is at most {limit} characters here");
        return name;
    }

    // The settings' trainer name as a DS OT, which holds 7 characters.
    static string DsOT(string name) => name.Length > 7 ? name[..7] : name;

    // -- Gen 5 ----------------------------------------------------------------------------------

    static PGF MakePgf(Game game, JsonObject r, string kind, string title)
    {
        var c = new PGF(new byte[PGF.Size])
        {
            CardID = Math.Clamp((int?)r["card_id"] ?? 2000, 0, 0xFFFF),
            CardTitle = title,
            Date = (string?)r["date"] is { Length: > 0 } d ? DateOnly.Parse(d) : DateOnly.FromDateTime(DateTime.Today),
            CardType = kind == "pokemon" ? 1 : 2,
            RestrictVersion = (byte)((int?)r["versions"] ?? 0),
            RestrictLanguage = (byte)((int?)r["restrict_language"] ?? 0),
            MultiObtain = r["multi_obtain"] is JsonValue v && v.TryGetValue(out bool multi) && multi,
            CardLocation = (int?)r["card_location"] ?? 0,       // 0xB2: set on official cards; kept, not shown
        };
        if (kind == "item")
        {
            c.ItemID = OneItem(r, 5).Item;
            return PadPgfText(c);
        }
        var species = DsSpecies(game, r);
        c.Species = species;
        c.Form = (byte)((int?)r["form"] ?? 0);
        // PKHeX.Core 26.10.18's PGF.Level reads 0x5B but its setter writes 0x5C, the egg flag: write the byte itself.
        var level = (byte)Math.Clamp((int?)r["level"] ?? 10, 1, 100);
        c.Data[PgfLevelAt] = level;
        c.MetLevel = (byte)Math.Clamp((int?)r["met_level"] ?? level, 0, 100);
        c.IsEgg = (bool?)r["is_egg"] ?? false;
        c.EggLocation = (ushort)((int?)r["egg_location"] ?? 0);
        c.Location = (ushort)((int?)r["met_location"] ?? 40001);               // "a lovely place"
        c.Ball = (byte)((int?)r["ball"] ?? 16);                                 // the Cherish Ball, as most Gen 5 cards
        c.HeldItem = (int?)r["held_item"] ?? 0;
        var moves = DsMoves(game, r, species, level);
        (c.Move1, c.Move2, c.Move3, c.Move4) = (moves[0], moves[1], moves[2], moves[3]);
        if (DsName(r, "nickname", 10, "a nickname") is { Length: > 0 } nick)
            c.Nickname = nick;
        c.Language = (int?)r["language"] ?? 0;
        var ot = DsName(r, "ot", 7, "an OT name");
        if (ot.Length > 0)
        {
            c.OriginalTrainerName = ot;
            c.TID16 = (ushort)((int?)r["tid"] ?? 12345);
            c.SID16 = (ushort)((int?)r["sid"] ?? 54321);
            c.OTGender = (byte)((int?)r["ot_gender"] ?? 0);
        }
        else if (c.IsEgg || r["tid"] is not null)
        {
            // An egg (or a card opened as it is): no OT, gender 3, as official Gen 5 eggs make the receiver the OT.
            c.TID16 = (ushort)((int?)r["tid"] ?? 0);
            c.SID16 = (ushort)((int?)r["sid"] ?? 0);
            c.OTGender = (byte)((int?)r["ot_gender"] ?? 3);
        }
        else
        {
            // Only eggs use that marker among PKHeX's 700 Gen 5 cards: a Pokemon for the receiver names the trainer,
            // as Gen 4 and 6 do.
            var tr = Trainer(game, r);
            (c.OriginalTrainerName, c.TID16, c.SID16, c.OTGender) = (DsOT(tr.OT), tr.TID16, tr.SID16, (byte)tr.Gender);
        }
        c.OriginGame = (byte)((int?)r["origin_game"] ?? 0);
        c.PIDType = ((string?)r["shiny"] ?? "random") switch
        {
            "random" => 1, "always" => 2, "never" => 0,
            var s => throw new ArgumentException($"Gen 5 cards are shiny at random, always or never, not {s}"),
        };
        c.PID = (uint)((long?)r["pid"] ?? 0);               // a few official cards carry one; kept, not shown
        c.Nature = (Nature)((int?)r["nature"] is { } n and >= 0 and < 25 ? n : 0xFF);  // 0xFF: random
        c.Gender = (byte)((int?)r["gender"] is { } g and (0 or 1) ? g : 2);          // 2: as the species' ratio decides
        c.AbilityType = ((string?)r["ability"] ?? "random12") switch
        {
            "1" => 0, "2" => 1, "hidden" => 2, "random12h" => 4, _ => 3,
        };
        if (Math.Clamp((int?)r["perfect_ivs"] ?? 0, 0, 3) > 0)
            throw new ArgumentException("Gen 5 cards cannot ask for a number of perfect IVs; set the IVs themselves");
        var ivs = SixFrom(r["ivs"], -1).Select(iv => iv is >= 0 and <= 31 ? iv : 0xFF).ToArray();   // display order
        (c.IV_HP, c.IV_ATK, c.IV_DEF, c.IV_SPA, c.IV_SPD, c.IV_SPE) = (ivs[0], ivs[1], ivs[2], ivs[3], ivs[4], ivs[5]);
        var contest = SixFrom(r["contest"], 0).Select(x => (byte)Math.Clamp(x, 0, 255)).ToArray();
        (c.ContestCool, c.ContestBeauty, c.ContestCute, c.ContestSmart, c.ContestTough, c.ContestSheen) =
            (contest[0], contest[1], contest[2], contest[3], contest[4], contest[5]);
        SetDsRibbons(c, r);
        return PadPgfText(c);
    }

    // A PGF's text fields as official cards hold them: the text, 0xFFFF, then 0xFF to the field's end (an unset field
    // all 0xFF, which the game reads as empty: no nickname, the species' name). A new card's zeros would not end.
    static readonly (int At, int Size)[] PgfTextFields = [(0x1E, 0x16), (0x4A, 0x10), (0x60, 0x4A)];   // nickname, OT, title

    static PGF PadPgfText(PGF c)
    {
        // An item card has no Pokemon, and official ones leave its nickname and OT as zeros: only the title is text.
        foreach (var (at, size) in c.IsEntity ? PgfTextFields : PgfTextFields[2..])
        {
            var field = c.Data.Slice(at, size);
            var end = 0;
            while (end + 1 < size && !(field[end] == 0xFF && field[end + 1] == 0xFF) && !(field[end] == 0 && field[end + 1] == 0))
                end += 2;
            field[end..].Fill(0xFF);
        }
        return c;
    }

    static void SetDsRibbons(object card, JsonObject r)
    {
        var ribbons = r["ribbons"] is JsonArray list ? list.Select(x => (string)x!).ToHashSet() : [];
        foreach (var name in ribbons)
            if (!RibbonNames.Contains(name))
                throw new ArgumentException($"no ribbon {name} (one of {string.Join(", ", RibbonNames)})");
        foreach (var name in RibbonNames)
            SetRibbon(card, name, ribbons.Contains(name));
    }

    // -- Gen 4 ----------------------------------------------------------------------------------

    // The official card a custom one is written onto: the first random-PID Pokemon card, or the first item card.
    static PCD Gen4Base(int giftType) => EncounterEvent.MGDB_G4.OfType<PCD>()
        .Where(p => (int)p.Gift.GiftType == giftType && (giftType != GiftPokemon4 || p.IsRandomPID()))
        .OrderBy(p => p.CardID).First();

    static PCD MakePcd(Game game, JsonObject r, string kind, string title)
    {
        var c = Gen4Base(kind == "pokemon" ? GiftPokemon4 : GiftItem4).Clone();
        c.CardID = Math.Clamp((int?)r["card_id"] ?? 2000, 0, 0xFFFF);
        c.CardTitle = title;
        c.GiftUsed = false;
        var data = c.Data;
        var games = (ushort)((int?)r["versions"] ?? 0);
        BitConverter.TryWriteBytes(data[PcdCompatibilityAt..], games == 0 ? Gen4AllGames : games);
        data.Slice(PcdIconsAt, 6).Clear();
        var gift = c.Gift;
        if (kind == "item")
        {
            var (item, _) = OneItem(r, 4);
            gift.GiftType = (GiftType4)GiftItem4;
            gift.ItemID = item;
            SetGen4Text(data, (string?)r["distribution_text"] is { Length: > 0 } itemText ? itemText
                : $"{Name(Strings.Item, item)} will be given to you as a gift!", 2);
            return c;
        }
        var species = DsSpecies(game, r);
        var pk = gift.PK;
        var trainer = Trainer(game, r);
        var language = (int?)r["language"] is { } l and > 0 ? l : trainer.Language > 0 ? trainer.Language : 2;
        pk.Species = species;
        pk.Form = (byte)((int?)r["form"] ?? 0);
        pk.Language = language;
        pk.CurrentLevel = (byte)Math.Clamp((int?)r["level"] ?? 10, 1, 100);
        pk.MetLevel = pk.CurrentLevel;                                          // the base card's is its own species'
        var moves = DsMoves(game, r, species, pk.CurrentLevel);
        (pk.Move1, pk.Move2, pk.Move3, pk.Move4) = (moves[0], moves[1], moves[2], moves[3]);
        pk.HealPP();
        pk.HeldItem = (int?)r["held_item"] ?? 0;
        pk.Ball = (byte)((int?)r["ball"] ?? pk.Ball);
        var nick = DsName(r, "nickname", 10, "a nickname");
        pk.IsNicknamed = nick.Length > 0;
        pk.Nickname = nick.Length > 0 ? nick : PKHeX.Core.SpeciesName.GetSpeciesNameGeneration(species, language, 4);
        var ot = DsName(r, "ot", 7, "an OT name");
        if (ot.Length > 0)
            (pk.OriginalTrainerName, pk.TID16, pk.SID16, pk.OriginalTrainerGender) =
                (ot, (ushort)((int?)r["tid"] ?? 12345), (ushort)((int?)r["sid"] ?? 54321), (byte)((int?)r["ot_gender"] ?? 0));
        else
            (pk.OriginalTrainerName, pk.TID16, pk.SID16, pk.OriginalTrainerGender) =
                (DsOT(trainer.OT), trainer.TID16, trainer.SID16, (byte)trainer.Gender);
        var ratio = game.Save().Personal[species].Gender;
        pk.PID = ((string?)r["shiny"] ?? "never") switch
        {
            "never" or "random" => 1u,
            "always" => ShinyPid4(r, species, pk.TID16, pk.SID16, ratio),
            "fixed" => (uint)((long?)r["pid"] ?? throw new ArgumentException("a fixed PID needs pid")),
            var s => throw new ArgumentException($"a Gen 4 card is never shiny, always shiny or a fixed PID, not {s}"),
        };
        // The gender the PK4 holds must be one the species can have: PKHeX rolls PIDs until it matches, so the base
        // card's (Manaphy's, genderless) would never end for Pikachu. A fixed PID decides it; else, as the species
        // allows (the game rolls the PID, and with it the gender, when the gift is received).
        pk.Gender = pk.PID != 1 ? EntityGender.GetFromPIDAndRatio(pk.PID, ratio)
                  : (byte)(ratio switch { PersonalInfo.RatioMagicGenderless => 2, PersonalInfo.RatioMagicFemale => 1, _ => 0 });
        pk.RefreshAbility((int)(pk.PID & 1));                                  // Gen 4: the PID's lowest bit picks it
        if (SixFrom(r["ivs"], -1).Any(iv => iv >= 0))
            throw new ArgumentException("Gen 4 cards cannot set IVs: the game rolls them when the gift is received");
        var contest = SixFrom(r["contest"], 0).Select(x => (byte)Math.Clamp(x, 0, 255)).ToArray();
        (pk.ContestCool, pk.ContestBeauty, pk.ContestCute, pk.ContestSmart, pk.ContestTough, pk.ContestSheen) =
            (contest[0], contest[1], contest[2], contest[3], contest[4], contest[5]);
        pk.RefreshChecksum();
        gift.PK = pk;
        SetDsRibbons(c, r);
        BitConverter.TryWriteBytes(data[(PcdIconsAt + 2)..], species);           // the middle icon, as official cards
        SetGen4Text(data, (string?)r["distribution_text"] is { Length: > 0 } text ? text
            : $"{Name(Strings.Species, species)} will be given to you as a gift!", language);
        return c;
    }

    // A PID shiny for the OT's IDs (Gen 4: TID ^ SID ^ high ^ low below 8) with the nature (PID % 25), gender (the low
    // byte against the species' ratio) and ability (bit 0) asked for. The search is seeded from the card's own fields,
    // so the same card always gets the same PID.
    static uint ShinyPid4(JsonObject r, ushort species, ushort tid, ushort sid, byte ratio)
    {
        var nature = (int?)r["nature"] is { } n and >= 0 and < 25 ? n : -1;
        var gender = (int?)r["gender"] is { } g and (0 or 1) ? g : -1;
        var ability = ((string?)r["ability"]) switch { "1" => 0, "2" => 1, _ => -1 };
        if (gender >= 0 && ratio == PersonalInfo.RatioMagicGenderless)
            throw new ArgumentException("this species has no gender");
        if ((gender == 1 && ratio == PersonalInfo.RatioMagicMale) || (gender == 0 && ratio == PersonalInfo.RatioMagicFemale))
            throw new ArgumentException(gender == 0 ? "this species is never male" : "this species is never female");
        var seed = (uint)(species * 2654435761u) ^ ((uint)tid << 16 | sid) ^ (uint)(nature + 1) * 40503u
                   ^ (uint)(gender + 2) * 2246822519u ^ (uint)(ability + 2) * 3266489917u;
        for (var i = 0; i < 1 << 24; i++)
        {
            seed = seed * 1103515245u + 12345u;
            var low = (ushort)(seed >> 16);
            seed = seed * 1103515245u + 12345u;
            var high = (ushort)(tid ^ sid ^ low ^ ((seed >> 16) & 7));
            var pid = (uint)high << 16 | low;
            if (pid <= 1)
                continue;
            if (nature >= 0 && pid % 25 != nature)
                continue;
            if (gender >= 0 && EntityGender.GetFromPIDAndRatio(pid, ratio) != gender)
                continue;
            if (ability >= 0 && (pid & 1) != ability)
                continue;
            return pid;
        }
        throw new ArgumentException("no shiny PID has that nature, gender and ability");
    }

    static void SetGen4Text(Span<byte> pcd, string text, int language)
    {
        var area = pcd.Slice(PcdTextAt, PcdTextSize);
        var limit = PcdTextSize / 2 - 1;
        if (text.Length > limit)
            throw new ArgumentException($"a Gen 4 card's text is at most {limit} characters");
        area.Fill(0xFF);
        StringConverter4.SetString(area, text.Replace("\r\n", "\n"), limit, language, StringConverterOption.None);
    }

    // -- card -> fields -------------------------------------------------------------------------

    static JsonObject DsCardFields(Game game, DataMysteryGift card, byte[]? trailer)
    {
        var f = new JsonObject { ["card_id"] = card.CardID, ["title"] = card.CardTitle.Trim(), ["multi_obtain"] = false };
        if (card is PGF p)
        {
            f["date"] = p.Date?.ToString("yyyy-MM-dd") ?? "";
            f["versions"] = (int)p.RestrictVersion;
            f["restrict_language"] = (int)p.RestrictLanguage;
            f["multi_obtain"] = p.MultiObtain;
            f["card_location"] = p.CardLocation;
            f["distribution_text"] = trailer is null ? "" : GetWifiText(trailer);
            f["kind"] = p.IsEntity ? "pokemon" : p.IsItem ? "item" : "other";       // an egg: pokemon with is_egg
            if (p.IsItem)
                f["items"] = new JsonArray(new JsonObject { ["item"] = p.ItemID, ["quantity"] = 1 });
            if (!p.IsEntity)
                return f;
            f["species"] = (int)p.Species;
            f["form"] = (int)p.Form;
            f["level"] = (int)p.Level;
            f["met_level"] = (int)p.MetLevel;
            f["is_egg"] = p.IsEgg;
            f["egg_location"] = (int)p.EggLocation;
            f["met_location"] = (int)p.Location;
            f["ball"] = (int)p.Ball;
            f["held_item"] = p.HeldItem;
            f["moves"] = new JsonArray((int)p.Move1, (int)p.Move2, (int)p.Move3, (int)p.Move4);
            f["nickname"] = p.IsNicknamed ? p.Nickname : "";
            f["language"] = p.Language;
            f["ot"] = p.OriginalTrainerName;
            f["tid"] = (int)p.TID16;
            f["sid"] = (int)p.SID16;
            f["ot_gender"] = (int)p.OTGender;
            f["origin_game"] = (int)p.OriginGame;
            f["shiny"] = p.PIDType switch { 2 => "always", 0 => "never", _ => "random" };
            f["pid"] = (long)p.PID;
            f["nature"] = (int)p.Nature < 25 ? (int)p.Nature : -1;
            f["gender"] = p.Gender is 0 or 1 ? p.Gender : -1;
            f["ability"] = p.AbilityType switch { 0 => "1", 1 => "2", 2 => "hidden", 4 => "random12h", _ => "random12" };
            int[] ivs = [p.IV_HP, p.IV_ATK, p.IV_DEF, p.IV_SPA, p.IV_SPD, p.IV_SPE];
            f["ivs"] = new JsonArray(ivs.Select(iv => (JsonNode)(iv <= 31 ? iv : -1)).ToArray());
            f["contest"] = new JsonArray((int)p.ContestCool, (int)p.ContestBeauty, (int)p.ContestCute, (int)p.ContestSmart,
                                         (int)p.ContestTough, (int)p.ContestSheen);
            f["ribbons"] = new JsonArray(RibbonNames.Where(n => GetRibbon(p, n)).Select(n => (JsonNode)n).ToArray());
            return f;
        }
        var c = (PCD)card;
        var games = BitConverter.ToUInt16(c.Data[PcdCompatibilityAt..]);
        f["date"] = "";
        f["versions"] = games == Gen4AllGames ? 0 : games;
        f["distribution_text"] = StringConverter4.GetString(c.Data.Slice(PcdTextAt, PcdTextSize));
        var gift = c.Gift;
        f["kind"] = (int)gift.GiftType switch { GiftPokemon4 => "pokemon", GiftItem4 => "item", _ => "other" };
        if ((int)gift.GiftType == GiftItem4)
            f["items"] = new JsonArray(new JsonObject { ["item"] = gift.ItemID, ["quantity"] = 1 });
        if ((int)gift.GiftType != GiftPokemon4)
            return f;
        var pk = gift.PK;
        f["species"] = (int)pk.Species;
        f["form"] = (int)pk.Form;
        f["level"] = (int)pk.CurrentLevel;
        f["ball"] = (int)pk.Ball;
        f["held_item"] = pk.HeldItem;
        f["moves"] = new JsonArray((int)pk.Move1, (int)pk.Move2, (int)pk.Move3, (int)pk.Move4);
        f["nickname"] = pk.IsNicknamed ? pk.Nickname : "";
        f["language"] = pk.Language;
        f["ot"] = pk.OriginalTrainerName;
        f["tid"] = (int)pk.TID16;
        f["sid"] = (int)pk.SID16;
        f["ot_gender"] = (int)pk.OriginalTrainerGender;
        f["shiny"] = pk.PID == 1 ? "never" : "fixed";
        f["pid"] = (long)pk.PID;
        f["contest"] = new JsonArray((int)pk.ContestCool, (int)pk.ContestBeauty, (int)pk.ContestCute, (int)pk.ContestSmart,
                                     (int)pk.ContestTough, (int)pk.ContestSheen);
        f["ribbons"] = new JsonArray(RibbonNames.Where(n => GetRibbon(c, n)).Select(n => (JsonNode)n).ToArray());
        return f;
    }

    // Gen 5 .bin text (Gifts.cs SetWifiText) back to a string; ``trailer`` is the 516 bytes after the card.
    static string GetWifiText(byte[] trailer)
    {
        var at = BinTextAt - PgfSize;
        var chars = new List<char>();
        for (var i = 0; i + 1 < BinTextSize; i += 2)
        {
            var ch = BitConverter.ToUInt16(trailer, at + i);
            if (ch == 0xFFFF)
                break;
            chars.Add(ch == 0xFFFE ? '\n' : (char)ch);
        }
        return new string(chars.ToArray());
    }
}

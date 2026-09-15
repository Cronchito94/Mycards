"""Tests du mapping TCGdex — le point délicat du lot 2.

Les cas couverts ne sont pas inventés : ce sont les configurations réellement
observées lors du relevé des 23 548 cartes du corpus (15/09/2026).
"""

from app.importers.tcgdex.mapping import (
    extract_finishes,
    normalize_code,
    number_sort,
    split_attributes,
)


class TestNormalizeCode:
    def test_supprime_les_accents(self) -> None:
        # « Métal » (FR) et « metal » (EN) doivent donner le même code, sinon
        # la langue importée décide du vocabulaire.
        assert normalize_code("Métal") == normalize_code("metal") == "metal"

    def test_espaces_en_tirets_bas(self) -> None:
        assert normalize_code("Holo Rare VMAX") == "holo_rare_vmax"

    def test_vide_et_none(self) -> None:
        assert normalize_code(None) is None
        assert normalize_code("  ") is None


class TestNumberSort:
    def test_numero_simple(self) -> None:
        assert number_sort("025") == 25

    def test_numero_prefixe(self) -> None:
        # Numéros réels du corpus : promos et galeries.
        assert number_sort("SV49") == 49
        assert number_sort("TG12") == 12
        assert number_sort("CC002") == 2

    def test_sans_chiffre(self) -> None:
        # None plutôt que 0 : un 0 fausserait le tri en le plaçant en tête.
        assert number_sort("?") is None
        assert number_sort(None) is None


class TestExtractFinishes:
    def test_cas_courant_normal_et_reverse(self) -> None:
        payload = {
            "variants": {"normal": True, "reverse": True, "holo": False,
                         "firstEdition": False, "wPromo": False},
            "variants_detailed": [
                {"type": "normal", "size": "standard", "variantId": "endfynwn4n10gzq",
                 "thirdParty": {"cardmarket": 483559, "tcgplayer": 219333}},
                {"type": "reverse", "size": "standard", "variantId": "cm4kqul3x1bwlz1f",
                 "thirdParty": {"cardmarket": 483559, "tcgplayer": 219333}},
            ],
        }
        specs, inconnus = extract_finishes(payload, "en")
        assert sorted(s.finish_code for s in specs) == ["normal", "reverse"]
        assert inconnus == []

    def test_first_edition_vient_des_booleens_seuls(self) -> None:
        # gym1-1 : `variants` porte firstEdition, `variants_detailed` l'ignore.
        # Sans l'union, 938 cartes perdraient leur 1st edition.
        payload = {
            "variants": {"firstEdition": True, "holo": True},
            "variants_detailed": [
                {"type": "holo", "size": "standard", "variantId": "jr7oetx1mqug9",
                 "thirdParty": {"tcgplayer": 83875}},
                {"type": "holo", "size": "standard", "variantId": "3a83wf50ts0izj268xwv3crwi",
                 "thirdParty": {"cardmarket": 274137, "tcgplayer": 83875}},
            ],
        }
        specs, _ = extract_finishes(payload, "en")
        assert sorted(s.finish_code for s in specs) == ["first_edition", "holo"]

    def test_doublons_fusionnes_en_gardant_tous_les_ids(self) -> None:
        # sv03.5-001 : deux entrées normal/standard, deux ids Cardmarket.
        payload = {
            "variants": {"normal": True},
            "variants_detailed": [
                {"type": "normal", "size": "standard", "variantId": "a",
                 "thirdParty": {"cardmarket": 733596}},
                {"type": "normal", "size": "standard", "variantId": "b",
                 "thirdParty": {"cardmarket": 720365}},
            ],
        }
        specs, _ = extract_finishes(payload, "en")
        assert len(specs) == 1
        assert specs[0].external_ids()["cardmarket"] == [720365, 733596]

    def test_jumbo_garde_le_code_de_finition_et_porte_sa_taille(self) -> None:
        # Depuis la migration 0006, le format ne suffixe plus la finition :
        # il vit dans `printing.size`, membre de la clé d'unicité.
        payload = {
            "variants": {},
            "variants_detailed": [{"type": "holo", "size": "jumbo", "variantId": "x"}],
        }
        specs, _ = extract_finishes(payload, "en")
        assert [(s.finish_code, s.size) for s in specs] == [("holo", "jumbo")]

    def test_jumbo_et_standard_restent_deux_impressions(self) -> None:
        """Le Bulbasaur du Set de Base existe dans les deux formats.

        Même finition, deux cotes sans rapport : elles ne doivent jamais
        fusionner, quel que soit le mécanisme qui les distingue.
        """
        payload = {
            "variants": {"normal": True},
            "variants_detailed": [
                {"type": "normal", "size": "standard", "variantId": "a"},
                {"type": "normal", "size": "jumbo", "variantId": "b"},
            ],
        }
        specs, _ = extract_finishes(payload, "en")
        assert sorted((s.finish_code, s.size) for s in specs) == [
            ("normal", "jumbo"),
            ("normal", "standard"),
        ]

    def test_type_francais_donne_le_meme_code(self) -> None:
        fr = {"variants": {}, "variants_detailed": [{"type": "Métal", "size": "Standard"}]}
        en = {"variants": {}, "variants_detailed": [{"type": "metal", "size": "standard"}]}
        assert extract_finishes(fr, "fr")[0][0].finish_code == "metal"
        assert extract_finishes(en, "en")[0][0].finish_code == "metal"

    def test_type_inconnu_remonte_sans_etre_invente(self) -> None:
        payload = {
            "variants": {},
            "variants_detailed": [{"type": "hologalactique", "size": "standard"}],
        }
        specs, inconnus = extract_finishes(payload, "en")
        assert specs == []
        assert inconnus == ["hologalactique"]

    def test_variant_id_generated_est_ignore(self) -> None:
        # `generated` est une valeur sentinelle portée par 8 861 cartes.
        payload = {
            "variants": {"normal": True},
            "variants_detailed": [
                {"type": "normal", "size": "standard", "variantId": "generated"}
            ],
        }
        specs, _ = extract_finishes(payload, "en")
        assert "tcgdexVariantIds" not in specs[0].external_ids()


class TestSplitAttributes:
    def test_separe_invariant_et_localise(self) -> None:
        payload = {
            "hp": 110, "retreat": 1, "dexId": [162], "regulationMark": "D",
            "stage": "Stage1", "types": ["Colorless"], "evolveFrom": "Sentret",
        }
        invariants, localises = split_attributes(payload)
        assert invariants == {"hp": 110, "retreat": 1, "dexId": [162],
                              "regulationMark": "D"}
        assert localises == {"stage": "Stage1", "types": ["Colorless"],
                             "evolveFrom": "Sentret"}

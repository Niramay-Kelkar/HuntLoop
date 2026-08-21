from huntloop.matching.normalize import normalize_employer_name


def test_amazon_development_center_variants_collapse_to_one_value():
    # Real raw EMPLOYER_NAME variants found in the ingested LCA data.
    variants = [
        "AMAZON DEVELOPMENT CENTER U.S., INC.",
        "Amazon Development Center U.S. Inc.",
        "Amazon Development Center U.S., Inc",
        "Amazon Development Center U.S., Inc.",
        "Amazon Development Center US Inc.",
        "Amazon Development Center US, Inc.",
    ]
    normalized = {normalize_employer_name(v) for v in variants}
    assert len(normalized) == 1


def test_amazon_com_services_variants_collapse_to_one_value():
    # Real raw EMPLOYER_NAME variants found in the ingested LCA data.
    variants = [
        "AMAZON.COM SERVICES LLC",
        "AMAZON.COM SERVICES LLC.",
        "AMAZON.COM SERVICES, LLC",
        "Amazon.com Services LLC",
        "Amazon.com Services, LLC",
    ]
    normalized = {normalize_employer_name(v) for v in variants}
    assert len(normalized) == 1


def test_does_not_merge_unrelated_companies():
    # False-positive risk case: mechanical normalization must not make two
    # genuinely different companies collide just because one contains the
    # other's name as a substring.
    a = normalize_employer_name("A&A INFOSYSTEMS, INC")
    b = normalize_employer_name("INFOSYS LIMITED")
    assert a != b


def test_case_insensitive():
    assert normalize_employer_name("acme corp") == normalize_employer_name("ACME CORP")


def test_punctuated_suffix_variant_matches_unpunctuated():
    assert normalize_employer_name("XYZ, L.L.C.") == normalize_employer_name("XYZ LLC")


def test_collapses_repeated_whitespace():
    assert normalize_employer_name("ACME   WIDGETS   INC") == normalize_employer_name("ACME WIDGETS INC")


def test_does_not_strip_suffix_word_mid_name():
    # "CO" here is part of the business name, not a trailing suffix.
    assert normalize_employer_name("CO OPERATIVE BANK") == "CO OPERATIVE BANK"


def test_strips_only_trailing_suffix_keeps_core_name():
    assert normalize_employer_name("ACME WIDGETS, INC.") == "ACME WIDGETS"

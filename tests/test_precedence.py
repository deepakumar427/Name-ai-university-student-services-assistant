"""Unit tests for the precedence engine (pure function, no database needed)."""

from app.policy.precedence import Claim, Scope, batch_covered, programme_covered, resolve

AS_OF = "2026-10-06"


def doc(doc_id, level, start, end="", supersedes="", programmes="ALL", batches="ALL"):
    return {"doc_id": doc_id, "authority_level": level, "effective_from": start, "effective_to": end,
            "supersedes": supersedes, "scope_programmes": programmes, "scope_batches": batches}


def claim(doc_id, level, start, value, section="1", end="", programmes="ALL", batches="ALL"):
    return Claim(claim_id=f"{doc_id}#{section}", doc_id=doc_id, section=section, authority_level=level,
                 effective_from=start, effective_to=end, scope_programmes=programmes, scope_batches=batches,
                 values=frozenset({value}) if value else frozenset())


def winner_ids(res):
    return [c.doc_id for c in res.winners]


def test_guide_example_supersession_beats_older_regulation_and_faq_loses_on_authority():
    docs = [doc("REG", 1, "2024-07-01"), doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2"), doc("FAQ", 4, "2026-09-15")]
    claims = [claim("REG", 1, "2024-07-01", "75", "7.2"), claim("CIRC", 2, "2026-08-01", "80"),
              claim("FAQ", 4, "2026-09-15", "65")]
    res = resolve(claims, docs, Scope(), AS_OF)
    assert winner_ids(res) == ["CIRC"]
    steps = {loser.claim.doc_id: loser.step for loser in res.losers}
    assert steps == {"REG": "supersession", "FAQ": "authority"}
    assert not res.flagged


def test_before_circular_starts_the_regulation_applies_and_circular_is_upcoming():
    docs = [doc("REG", 1, "2024-07-01"), doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2")]
    claims = [claim("REG", 1, "2024-07-01", "75", "7.2"), claim("CIRC", 2, "2026-08-01", "80")]
    res = resolve(claims, docs, Scope(), "2026-07-15")
    assert winner_ids(res) == ["REG"]
    assert [c.doc_id for c in res.upcoming] == ["CIRC"]


def test_expired_document_is_excluded():
    claims = [claim("OLD", 1, "2020-01-01", "70", end="2024-06-30"), claim("NEW", 1, "2024-07-01", "75")]
    res = resolve(claims, [], Scope(), AS_OF)
    assert winner_ids(res) == ["NEW"]
    assert [c.doc_id for c, _ in res.excluded] == ["OLD"]


def test_higher_authority_wins_even_if_older():
    claims = [claim("REG", 1, "2020-01-01", "75"), claim("NOTICE", 3, "2026-09-01", "70")]
    res = resolve(claims, [], Scope(), AS_OF)
    assert winner_ids(res) == ["REG"]
    assert res.losers[0].step == "authority"


def test_same_authority_newer_wins():
    claims = [claim("C1", 2, "2025-01-01", "75"), claim("C2", 2, "2026-01-01", "78")]
    res = resolve(claims, [], Scope(), AS_OF)
    assert winner_ids(res) == ["C2"]
    assert res.losers[0].step == "recency"


def test_same_level_same_date_different_values_is_flagged():
    claims = [claim("A", 2, "2026-09-01", "1000"), claim("B", 2, "2026-09-01", "1500")]
    res = resolve(claims, [], Scope(), AS_OF)
    assert res.flagged


def test_low_authority_document_cannot_supersede():
    docs = [doc("REG", 1, "2024-07-01"), doc("FAQ", 4, "2026-09-15", supersedes="REG")]
    claims = [claim("REG", 1, "2024-07-01", "75"), claim("FAQ", 4, "2026-09-15", "65")]
    res = resolve(claims, docs, Scope(), AS_OF)
    assert winner_ids(res) == ["REG"]
    assert res.losers[0].step == "authority"  # not supersession


def test_superseding_document_must_itself_be_in_force():
    docs = [doc("REG", 1, "2024-07-01"), doc("CIRC", 2, "2027-01-01", supersedes="REG")]
    claims = [claim("REG", 1, "2024-07-01", "75")]
    res = resolve(claims, docs, Scope(), AS_OF)
    assert winner_ids(res) == ["REG"]


def test_clause_supersession_does_not_remove_other_clauses():
    docs = [doc("REG", 1, "2024-07-01"), doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2")]
    claims = [claim("REG", 1, "2024-07-01", None, "7.3"), claim("REG", 1, "2024-07-01", "75", "7.2")]
    res = resolve(claims, docs, Scope(), AS_OF)
    assert [c.section for c in res.winners] == ["7.3"]


def test_level_five_is_informational_only():
    claims = [claim("FORUM", 5, "2026-09-30", "50"), claim("REG", 1, "2024-07-01", "75")]
    res = resolve(claims, [], Scope(), AS_OF)
    assert winner_ids(res) == ["REG"]
    assert [c.doc_id for c in res.informational] == ["FORUM"]


def test_programme_and_batch_scope():
    claims = [claim("ECE", 2, "2026-01-01", "70", programmes="B.Tech ECE"), claim("ALL", 1, "2024-07-01", "75")]
    res = resolve(claims, [], Scope("B.Tech CSE", 2025), AS_OF)
    assert winner_ids(res) == ["ALL"]
    assert programme_covered("B.Tech", "B.Tech CSE")
    assert batch_covered("2023+", 2025) and not batch_covered("2023+", 2022)
    assert batch_covered("2022-2024", 2023) and batch_covered("ALL", 2019)


def test_same_inputs_same_output():
    claims = [claim("A", 2, "2026-09-01", "1000"), claim("B", 2, "2026-08-01", "1500")]
    first = resolve(claims, [], Scope(), AS_OF)
    second = resolve(list(reversed(claims)), [], Scope(), AS_OF)
    assert winner_ids(first) == winner_ids(second) == ["A"]

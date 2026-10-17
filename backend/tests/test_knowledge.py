from app import knowledge


def test_every_entry_has_source_info_and_valid_category():
    kb = knowledge.load_kb()
    ids = set()
    for e in kb["entries"]:
        assert e["category"] in knowledge.CATEGORIES
        assert e["retrieved"] == "2026-10-17"
        assert e["id"] not in ids
        ids.add(e["id"])
        if e["source_type"] == "sportfit_website":
            assert e["source_url"].startswith("https://sportfitkw.com/")
        else:
            assert e["source_type"] == "project_authored"


def test_no_placeholder_contact_details_in_kb():
    text = str(knowledge.load_kb())
    assert "111 111 111" not in text and "+11111111111" not in text


def test_upholstery_is_grounded():
    hits = knowledge.search("do you repair upholstery on benches", categories=("services",))
    assert hits and hits[0]["id"] == "svc-upholstery-repair"
    assert hits[0]["source_url"] == "https://sportfitkw.com/service.html"


def test_spare_parts_returns_honest_limitation_not_a_claim():
    hits = knowledge.search("do you sell spare parts", categories=("services", "limitations"))
    assert hits and hits[0]["category"] == "limitations"
    assert "not been verified" in hits[0]["content"] or "not verified" in hits[0]["content"].lower()


def test_unrelated_query_has_no_match():
    assert knowledge.search("what is the weather on mars") == []
    assert knowledge.search("   ") == []


def test_safety_search_finds_electrical_guidance():
    hits = knowledge.search("my treadmill smells like burning", categories=("safety",))
    assert hits and hits[0]["id"] == "safety-electrical"

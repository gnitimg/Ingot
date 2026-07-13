from app.services.graph_rag import GraphRAGService, normalize_entity_name, parse_json_object


def test_parse_json_from_markdown_fence():
    result = parse_json_object('```json\n{"entities": [], "relationships": []}\n```')
    assert result == {"entities": [], "relationships": []}


def test_normalize_entity_name():
    assert normalize_entity_name("  Silicon   Flow ") == "silicon flow"


def test_detect_communities_separates_components():
    entity_ids = ["a", "b", "c", "d"]
    relations = [
        {"source_id": "a", "target_id": "b", "weight": 1},
        {"source_id": "c", "target_id": "d", "weight": 1},
    ]
    communities = GraphRAGService._detect_communities(entity_ids, relations)
    assert {frozenset(group) for group in communities} == {frozenset({"a", "b"}), frozenset({"c", "d"})}


from app.services.retrieval import RetrievalService


def empty_retrieval():
    return {
        "chunks": [],
        "facts": [],
        "communities": [],
        "rerank_used": False,
        "warnings": [],
    }


def test_build_messages_keeps_up_to_twenty_recent_history_messages():
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index}",
        }
        for index in range(24)
    ]

    messages = RetrievalService.build_messages(
        "current question",
        empty_retrieval(),
        history,
    )

    remembered = messages[1:-1]
    assert len(remembered) == 20
    assert remembered[0]["content"] == "turn-4"
    assert remembered[-1]["content"] == "turn-23"


def test_build_messages_bounds_total_history_size():
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": str(index) * 6000,
        }
        for index in range(20)
    ]

    messages = RetrievalService.build_messages(
        "current question",
        empty_retrieval(),
        history,
    )

    remembered = messages[1:-1]
    assert sum(len(message["content"]) for message in remembered) <= 24000
    assert remembered[-1]["content"].startswith("19")

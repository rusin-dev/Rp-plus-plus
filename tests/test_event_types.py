from src.core.event_bus import EventTypes


def test_subagent_event_types_defined():
    names = [
        "SUBAGENT_START",
        "SUBAGENT_TOKEN",
        "SUBAGENT_TOOL_CALL",
        "SUBAGENT_TOOL_RESULT",
        "SUBAGENT_DONE",
        "SUBAGENT_ERROR",
    ]
    for name in names:
        value = getattr(EventTypes, name)
        assert isinstance(value, str) and value.startswith("subagent_")

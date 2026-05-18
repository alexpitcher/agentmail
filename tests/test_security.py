from agentmail.security import sanitize_filename, split_route


def test_sanitize_filename_strips_paths_and_controls() -> None:
    assert sanitize_filename("../../evil.sh") == "evil.sh"
    assert sanitize_filename("..\x00/brief.txt") == "brief.txt"


def test_split_route_extracts_plus_tag() -> None:
    assert split_route("bot+clientsite@alexpitcher.co.uk") == ("bot", "clientsite", "alexpitcher.co.uk")
    assert split_route("bot@alexpitcher.co.uk") == ("bot", None, "alexpitcher.co.uk")

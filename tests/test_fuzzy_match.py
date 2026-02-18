"""Tests for _select_fuzzy_match() confidence gating."""


from UnrealAgent.mcp_server import _select_fuzzy_match


def _make_result(name: str, path: str, score: float) -> dict:
    return {"name": name, "path": f"/Game/{path}", "score": score}


class TestSelectFuzzyMatch:
    def test_empty_results_returns_none(self):
        assert _select_fuzzy_match([], "anything") is None

    def test_exact_name_substring_match(self):
        """Query is a substring of the top result name → accept."""
        results = [
            _make_result("W_Healthbar", "UI/W_Healthbar", 0.8),
            _make_result("W_HealthbarMini", "UI/W_HealthbarMini", 0.78),
        ]
        match = _select_fuzzy_match(results, "W_Healthbar")
        assert match is not None
        assert match["name"] == "W_Healthbar"

    def test_name_contains_query(self):
        """Result name contains the query string → accept."""
        results = [
            _make_result("W_ShooterHUDLayout", "UI/W_ShooterHUDLayout", 0.9),
            _make_result("W_ShooterScoreboard", "UI/W_ShooterScoreboard", 0.85),
        ]
        match = _select_fuzzy_match(results, "ShooterHUD")
        assert match is not None
        assert match["name"] == "W_ShooterHUDLayout"

    def test_strong_score_gap_no_name_match(self):
        """Large score gap between top and second result → accept."""
        results = [
            _make_result("IA_Jump", "Input/IA_Jump", 0.95),
            _make_result("IA_Crouch", "Input/IA_Crouch", 0.6),
        ]
        match = _select_fuzzy_match(results, "input action jump")
        assert match is not None
        assert match["name"] == "IA_Jump"

    def test_ambiguous_cluster_returns_none(self):
        """Garbage query with tightly clustered scores → reject."""
        results = [
            _make_result("BP_PlayerController", "Core/BP_PlayerController", 0.5),
            _make_result("BP_PlayerCharacter", "Core/BP_PlayerCharacter", 0.48),
            _make_result("BP_PlayerState", "Core/BP_PlayerState", 0.47),
        ]
        match = _select_fuzzy_match(results, "completely made up thing")
        assert match is None

    def test_single_result_no_name_match_returns_none(self):
        """Single result with no name match → can't assess confidence."""
        results = [
            _make_result("BP_GameMode", "Core/BP_GameMode", 1.0),
        ]
        match = _select_fuzzy_match(results, "random garbage query")
        assert match is None

    def test_query_is_superset_of_name(self):
        """Query contains the result name → accept (vice versa substring)."""
        results = [
            _make_result("IA_Jump", "Input/IA_Jump", 0.9),
            _make_result("IA_Crouch", "Input/IA_Crouch", 0.88),
        ]
        match = _select_fuzzy_match(results, "IA_Jump asset")
        assert match is not None
        assert match["name"] == "IA_Jump"

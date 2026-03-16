from pathlib import Path
import pytest
import json
from alyx_connector.files.validation.rules import Rule


@pytest.fixture
def rule_file() -> dict:
    rule_path = Path(__file__).parent / "rules.json"
    with open(rule_path, "r") as f:
        return json.load(f)


def test_rule_instanciation(rule_file: dict):

    rules = rule_file["rules"]
    rule = Rule.parse_from_dict(
        "imaging_data_rule1",
        rules["imaging_data_rule1"],
    )

    assert True

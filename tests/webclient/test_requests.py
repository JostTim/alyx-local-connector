import responses, requests, pytest
from typing import cast
from alyx_connector import Connector
from pathlib import Path
from responses.matchers import urlencoded_params_matcher, header_matcher, query_param_matcher

SERVER_URL = "http://127.0.0.1:80"
VALID_TOKEN = "1234657988abcbdefg"

AUTH_MATCHER = header_matcher(
    {
        "Authorization": f"Token {VALID_TOKEN}",
        "Accept": "application/json",
    }
)


@pytest.fixture
def api_schema():
    file_path = Path(__file__).parent / "alyx-api-schema.yaml"
    with open(file_path, "r") as file:
        return file.read()


@pytest.fixture
@responses.activate
def connector():

    username = "test_user"
    password = "123465789"

    responses.add(
        method=responses.POST,
        url=SERVER_URL + "/auth-token",
        json={"token": VALID_TOKEN},
        status=201,
        match=[urlencoded_params_matcher({"username": username, "password": password})],  # Add matcher for request data
    )

    connector = Connector.setup(
        username=username, url=SERVER_URL, password=password, make_default=True, force_prompt=False
    )
    connector.web_client.authenticate(password=password, force=True)
    return connector


@pytest.fixture
@responses.activate
def connector_with_schema(connector: Connector, api_schema):
    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/api/schema",
        body=api_schema,
        status=200,
        content_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="api-schema.yaml"'},
    )

    # get schema through the @property schema
    connector.web_client.schema

    return connector


@responses.activate
def test_responses_configuration():

    username = "test_user"
    password = "123465789"

    responses.add(
        method=responses.POST,
        url=SERVER_URL + "/auth-token",
        json={"token": VALID_TOKEN},
        status=201,
        match=[urlencoded_params_matcher({"username": username, "password": password})],  # Add matcher for request data
    )

    resp = requests.post(SERVER_URL + "/auth-token", data={"username": username, "password": password})
    token = cast(dict, resp.json()).get("token")
    assert token == VALID_TOKEN

    with pytest.raises(requests.exceptions.ConnectionError):
        resp = requests.post(SERVER_URL + "/auth-token", data={"username": username, "password": "wrong_pass"})


def test_connector_configuration(connector: Connector):
    assert connector.web_client.is_logged_in()


@responses.activate
def test_invalid_connector_username_configuration():

    wrong_username = "wrong_user"
    wrong_password = "wrong_password"

    user_password_matcher = urlencoded_params_matcher({"username": wrong_username, "password": wrong_password})
    # correct values in the server
    responses.add(
        method=responses.POST,
        url=SERVER_URL + "/auth-token",
        json={"error": "user not present"},
        status=404,
        match=[user_password_matcher],
    )
    with pytest.raises(requests.exceptions.HTTPError):
        Connector.setup(
            username=wrong_username, url=SERVER_URL, password=wrong_password, make_default=True, force_prompt=False
        )


@responses.activate
def test_connector_simple_session_search(connector_with_schema: Connector):

    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/sessions",
        match=[AUTH_MATCHER],
        json=[
            {"id": "516854984651984", "subject": "ea04", "date": "2023-11-21", "number": 1},
            {"id": "168498484198415", "subject": "ea04", "date": "2023-10-02", "number": 3},
            {"id": "441684446184948", "subject": "ea03", "date": "2024-01-16", "number": 2},
        ],
        status=200,
    )

    sessions = connector_with_schema.search(endpoint="sessions", details=False)
    assert sorted(sessions["subject"].unique().tolist()) == ["ea03", "ea04"]


@responses.activate
def test_connector_simple_filtered_session_search(connector_with_schema: Connector):

    subject_matcher = query_param_matcher({"subject": "ea04"})

    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/sessions",
        match=[AUTH_MATCHER, subject_matcher],
        json=[
            {"id": "516854984651984", "subject": "ea04", "date": "2023-11-21", "number": 1},
            {"id": "168498484198415", "subject": "ea04", "date": "2023-10-02", "number": 3},
        ],
        status=200,
    )

    sessions = connector_with_schema.search(endpoint="sessions", subject="ea04", details=False)
    assert sorted(sessions["subject"].unique().tolist()) == ["ea04"]


@responses.activate
def test_connector_detailed_filtered_session_search(connector_with_schema: Connector):

    subject_matcher = query_param_matcher({"subject": "ea04"})

    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/sessions",
        match=[AUTH_MATCHER, subject_matcher],
        json=[
            {"id": "516854984651984", "subject": "ea04", "date": "2023-11-21", "number": 1},
            {"id": "168498484198415", "subject": "ea04", "date": "2023-10-02", "number": 3},
        ],
        status=200,
    )

    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/sessions/516854984651984",
        match=[AUTH_MATCHER],
        json={
            "id": "516854984651984",
            "subject": "ea04",
            "date": "2023-11-21",
            "number": 1,
            "narrative": "a good session",
            "qc": "PASS",
        },
        status=200,
    )

    responses.add(
        method=responses.GET,
        url=SERVER_URL + "/sessions/168498484198415",
        match=[AUTH_MATCHER],
        json={
            "id": "168498484198415",
            "subject": "ea04",
            "date": "2023-10-02",
            "number": 3,
            "narrative": "a bad session",
            "qc": "FAIL",
        },
        status=200,
    )

    sessions = connector_with_schema.search(endpoint="sessions", subject="ea04", details=True)
    assert sorted(sessions["subject"].unique().tolist()) == ["ea04"]
    assert sorted(sessions["qc"].unique().tolist()) == ["FAIL", "PASS"]

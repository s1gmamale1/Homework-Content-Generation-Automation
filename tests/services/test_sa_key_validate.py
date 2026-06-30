import json
import pytest
from app.services.sa_key_validate import parse_and_validate_sa_key, InvalidServiceAccountKey


def _good() -> bytes:
    return json.dumps({
        "type": "service_account",
        "project_id": "my-proj",
        "client_email": "svc@my-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


def test_valid_key_returns_project_and_email():
    assert parse_and_validate_sa_key(_good()) == (
        "my-proj", "svc@my-proj.iam.gserviceaccount.com"
    )


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("type"),
    lambda d: d.update(type="authorized_user"),
    lambda d: d.pop("project_id"),
    lambda d: d.pop("client_email"),
    lambda d: d.pop("private_key"),
    lambda d: d.update(project_id=""),
])
def test_rejects_non_sa(mutate):
    d = json.loads(_good())
    mutate(d)
    with pytest.raises(InvalidServiceAccountKey):
        parse_and_validate_sa_key(json.dumps(d).encode())


def test_rejects_non_json():
    with pytest.raises(InvalidServiceAccountKey):
        parse_and_validate_sa_key(b"not json {{{")

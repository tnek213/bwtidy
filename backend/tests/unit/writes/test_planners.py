"""The plan of a moveToPersonal. Those of a convertItem and an applyTemplate are tested in
test_create.py and test_templates.py."""

import copy
from typing import Any

from bwtidy.writes.planners import plan_personal
from tests.support.sample_vault import LOGIN


def test_move_to_personal_drops_the_passkeys():
    login: dict[str, Any] = {
        **copy.deepcopy(LOGIN),
        "organizationId": "o1",
        "collectionIds": ["c1"],
    }
    new = plan_personal(login)
    assert "fido2Credentials" not in new["login"]
    assert new["login"]["password"] == LOGIN["login"]["password"]
    assert new["login"]["uris"] == LOGIN["login"]["uris"]
    assert (new["organizationId"], new["collectionIds"]) == (None, [])
    ## the item it was planned from stays as it was
    assert login["login"]["fido2Credentials"] == LOGIN["login"]["fido2Credentials"]

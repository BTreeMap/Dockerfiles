"""The declared shapes of everything this project receives from elsewhere.

Three sources, none of them trustworthy: a peer's steal request and health
report, and Cloudflare's registration API. All three used to be navigated with
`.get()` chains and bare `try/except`, which is why the cases below are worth
stating -- each one is a payload that used to produce either a wrong answer or
an exception from a place that could not handle it.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ci.egress import _ENROLMENT, _PROVISIONED
from ci.mesh import HealthReport, StealRequest, StealResponse

# --- a peer's steal request -------------------------------------------------


def test_an_absent_count_defaults_to_one() -> None:
    assert StealRequest.model_validate_json(b"{}").count == 1


def test_a_stated_count_is_honoured() -> None:
    assert StealRequest.model_validate_json(b'{"count": 4}').count == 4


@pytest.mark.parametrize("body", [b'{"count": 0}', b'{"count": -5}'])
def test_a_non_positive_count_is_rejected_rather_than_served(body: bytes) -> None:
    """`ge=1` replaces a `max(1, ...)` clamp and reaches the same value.

    The caller falls back to one task, so a peer asking for zero still gets the
    single task the old clamp would have given it.
    """
    with pytest.raises(ValidationError):
        StealRequest.model_validate_json(body)


@pytest.mark.parametrize(
    "body",
    [
        b'{"count": "many"}',
        b'{"count": null}',
        b"[]",           # an array, not an object
        b"not json",
    ],
)
def test_a_malformed_request_is_rejected_not_guessed_at(body: bytes) -> None:
    """Every one of these previously landed in `except (ValueError, TypeError,
    AttributeError)` -- catching AttributeError being the admission that the
    code could not say what type it was holding."""
    with pytest.raises(ValidationError):
        StealRequest.model_validate_json(body)


# --- a victim's response ----------------------------------------------------


def test_a_response_without_tasks_is_an_empty_handover() -> None:
    assert StealResponse.model_validate_json(b"{}").tasks == ()


def test_task_elements_stay_opaque_so_one_bad_entry_costs_only_itself() -> None:
    """Fail-soft is deliberate here: rejecting the envelope would discard work
    the peer has already given up, which is strictly worse than dropping one."""
    payload = json.dumps({"tasks": [{"image": "redis"}, "junk", 7]}).encode()
    assert len(StealResponse.model_validate_json(payload).tasks) == 3


# --- a peer's health --------------------------------------------------------


def test_health_defaults_are_safe_when_fields_are_missing() -> None:
    report = HealthReport.model_validate_json(b"{}")
    assert report.spare == 0 and report.worker_id == -1


def test_a_negative_spare_count_is_rejected() -> None:
    """`spare` is the evidence `peers_drained` rests on.

    A negative count is nonsense, and the old `int(...)` accepted it silently --
    which would have read as "emptier than empty" to a caller deciding whether
    the run was finished.
    """
    with pytest.raises(ValidationError):
        HealthReport.model_validate_json(b'{"spare": -1}')


def test_health_round_trips_through_its_own_schema() -> None:
    encoded = HealthReport(worker_id=2, spare=5).model_dump()
    assert HealthReport.model_validate(encoded) == HealthReport(worker_id=2, spare=5)


# --- Cloudflare's registration API ------------------------------------------


def test_an_enrolment_needs_both_an_id_and_a_token() -> None:
    with pytest.raises(ValidationError):
        _ENROLMENT.validate_json(b'{"id": "abc"}')
    with pytest.raises(ValidationError):
        _ENROLMENT.validate_json(b'{"id": "", "token": "t"}')


def test_an_enrolment_without_a_policy_is_not_masque() -> None:
    """A missing policy must read as "not yet MASQUE", so the PATCH asserts it."""
    enrolment = _ENROLMENT.validate_json(b'{"id": "abc", "token": "t"}')
    assert enrolment.policy.tunnel_protocol != "masque"


def test_an_existing_masque_policy_is_detected() -> None:
    payload = json.dumps(
        {"id": "abc", "token": "t", "policy": {"tunnel_protocol": "masque"}}
    ).encode()
    assert _ENROLMENT.validate_json(payload).policy.tunnel_protocol == "masque"


def test_addresses_are_absent_rather_than_invented_when_not_returned() -> None:
    """The caller applies the fallback with `or`, so absent must stay falsy."""
    assignment = _PROVISIONED.validate_json(b"{}")
    assert assignment.config.interface.addresses.v4 is None
    assert assignment.config.interface.addresses.v6 is None


def test_addresses_are_read_from_a_full_response() -> None:
    payload = json.dumps(
        {"config": {"interface": {"addresses": {"v4": "10.0.0.2", "v6": "fd00::9"}}}}
    ).encode()
    addresses = _PROVISIONED.validate_json(payload).config.interface.addresses
    assert (addresses.v4, addresses.v6) == ("10.0.0.2", "fd00::9")


def test_an_empty_address_falls_back_like_a_missing_one() -> None:
    """Preserves the `or` the old `.get()` chain applied: "" is not an address."""
    payload = json.dumps({"config": {"interface": {"addresses": {"v4": ""}}}}).encode()
    assert not _PROVISIONED.validate_json(payload).config.interface.addresses.v4


def test_a_config_of_the_wrong_type_is_a_validation_error_not_an_attribute_error() -> None:
    """The latent crash this modelling removed.

    `patched.json().get("config", {})` returned the string, and the next `.get()`
    in the chain raised AttributeError -- which `register` does not catch, since
    it guards only HTTPError and ValueError. As a ValidationError it is a
    ValueError, so it now degrades the tunnel instead of escaping the step.
    """
    with pytest.raises(ValidationError) as caught:
        _PROVISIONED.validate_json(b'{"config": "not an object"}')
    assert isinstance(caught.value, ValueError)

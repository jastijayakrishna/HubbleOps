from __future__ import annotations

from hubbleops.core.repair import TransformInput, TransformOutput
from hubbleops.packs.google_ads.repairs import (
    RestPathTransform,
    SdkPinTransform,
    SubjectRenameTransform,
    VersionLiteralTransform,
    transforms,
)
from hubbleops.repair import deterministic

MINIMUMS = {"v25": {"python": "31.2.0", "php": "33.6.0", "java": "44.0.0"}}


def request(
    text: str,
    *,
    path: str = "src/client.py",
    claim_type: str = "call_version",
    line: int | None = 1,
    subject: str | None = None,
    replacement: str | None = None,
    obligation_id: str = "a" * 64,
) -> TransformInput:
    return TransformInput(
        obligation_id=obligation_id,
        path=path,
        text=text,
        current_state="fixture",
        required_state="fixture",
        from_version="v22",
        to_version="v25",
        claim_type=claim_type,
        line=line,
        subject=subject,
        replacement=replacement,
    )


def test_a_version_literal_is_rewritten_in_place() -> None:
    subject = request('client.get_service("GoogleAdsService", version="v22")\n')
    transform = VersionLiteralTransform()
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert produced.text == 'client.get_service("GoogleAdsService", version="v25")\n'
    assert transform.postcondition(produced) is True


def test_a_longer_token_that_merely_starts_with_the_version_is_untouched() -> None:
    subject = request('code = "v220"\n')
    assert VersionLiteralTransform().precondition(subject) is False


def test_a_generated_namespace_segment_is_rewritten_as_a_version_token() -> None:
    subject = request("from google.ads.googleads.v22.services import x\n")
    produced = VersionLiteralTransform().apply(subject)
    assert produced.text == "from google.ads.googleads.v25.services import x\n"


def test_an_upper_case_namespace_segment_is_rewritten_preserving_its_case() -> None:
    subject = request(
        "use Google\\Ads\\GoogleAds\\V22\\Services\\GoogleAdsServiceClient;\n",
        path="src/Ads.php",
    )
    transform = VersionLiteralTransform()
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert produced.text == "use Google\\Ads\\GoogleAds\\V25\\Services\\GoogleAdsServiceClient;\n"
    assert transform.postcondition(produced) is True


def test_a_branch_alias_pin_is_never_rewritten_into_a_release_that_does_not_exist() -> None:
    subject = request(
        '"googleads/google-ads-php": "dev-legacy-v32.1.0"\n',
        path="composer.json",
        claim_type="sdk_installed",
    )
    assert SdkPinTransform(minimums=MINIMUMS).precondition(subject) is False


def test_a_caret_pin_below_the_php_minimum_is_raised() -> None:
    subject = request(
        '"googleads/google-ads-php": "^32.1.0"\n', path="composer.json", claim_type="sdk_installed"
    )
    transform = SdkPinTransform(minimums=MINIMUMS)
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert produced.text == '"googleads/google-ads-php": "^33.6.0"\n'
    assert transform.postcondition(produced) is True


def test_a_rest_path_is_reversioned_only_after_a_slash() -> None:
    subject = request(
        'url = "https://googleads.googleapis.com/v22/customers"\n',
        claim_type="endpoint_reference",
    )
    transform = RestPathTransform()
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert "/v25/customers" in produced.text
    assert transform.postcondition(produced) is True


def test_a_rest_transform_declines_a_version_that_is_not_a_path_segment() -> None:
    subject = request('label = "v22"\n', claim_type="endpoint_reference")
    assert RestPathTransform().precondition(subject) is False


def test_a_renamed_subject_is_replaced_and_proved_gone() -> None:
    subject = request(
        "SELECT campaign.old FROM campaign\n",
        claim_type="request_text",
        subject="campaign.old",
        replacement="campaign.new",
    )
    transform = SubjectRenameTransform()
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert produced.text == "SELECT campaign.new FROM campaign\n"
    assert transform.postcondition(produced) is True


def test_a_removal_with_no_replacement_is_never_claimed_by_the_rename_transform() -> None:
    subject = request(
        "SELECT campaign.legacy FROM campaign\n",
        claim_type="request_text",
        subject="campaign.legacy",
        replacement=None,
    )
    assert SubjectRenameTransform().precondition(subject) is False


def test_an_sdk_pin_below_the_documented_minimum_is_raised() -> None:
    subject = request("google-ads==22.1.0\n", path="requirements.txt", claim_type="sdk_installed")
    transform = SdkPinTransform(minimums=MINIMUMS)
    assert transform.precondition(subject) is True
    produced = transform.apply(subject)
    assert produced.text == "google-ads==31.2.0\n"
    assert transform.postcondition(produced) is True


def test_an_sdk_pin_already_above_the_minimum_is_left_alone() -> None:
    subject = request("google-ads==33.0.0\n", path="requirements.txt", claim_type="sdk_installed")
    assert SdkPinTransform(minimums=MINIMUMS).precondition(subject) is False


def test_a_language_with_no_documented_minimum_is_never_guessed() -> None:
    subject = request(
        '"google-ads-api": "^17.1.0"\n', path="package.json", claim_type="sdk_installed"
    )
    assert SdkPinTransform(minimums=MINIMUMS).precondition(subject) is False


def test_an_obligation_no_transform_claims_stays_open_for_a_human() -> None:
    report = deterministic.run(
        transforms=transforms(MINIMUMS),
        requests=[request("nothing to do here\n", claim_type="config_reference")],
    )
    assert report.outcomes[0].result == deterministic.NO_TRANSFORM
    assert report.discharged() == ()
    assert report.texts == {}


def test_a_failing_postcondition_reverts_and_writes_nothing() -> None:
    class Liar:
        name = "liar"
        failure_class = "call_version"

        def precondition(self, subject: TransformInput) -> bool:
            return True

        def apply(self, subject: TransformInput) -> TransformOutput:
            return TransformOutput(
                source=subject, result="APPLIED", text="corrupted", reason="wrote nonsense"
            )

        def postcondition(self, subject: TransformOutput) -> bool:
            return False

    report = deterministic.run(transforms=[Liar()], requests=[request("original\n")])
    assert report.outcomes[0].result == deterministic.POSTCONDITION_REVERTED
    assert report.texts == {}


def test_a_throwing_transform_fails_closed_rather_than_crashing_the_run() -> None:
    class Explosive:
        name = "explosive"
        failure_class = "call_version"

        def precondition(self, subject: TransformInput) -> bool:
            return True

        def apply(self, subject: TransformInput) -> TransformOutput:
            raise RuntimeError("boom")

        def postcondition(self, subject: TransformOutput) -> bool:
            return True

    report = deterministic.run(transforms=[Explosive()], requests=[request("original\n")])
    assert report.outcomes[0].result == deterministic.TRANSFORM_FAILED
    assert "boom" in report.outcomes[0].reason
    assert report.texts == {}


def test_two_obligations_on_one_file_thread_the_text_through() -> None:
    text = 'a = "v22"\nb = "v22"\n'
    report = deterministic.run(
        transforms=transforms(MINIMUMS),
        requests=[
            request(text, line=1, obligation_id="a" * 64),
            request(text, line=2, obligation_id="b" * 64),
        ],
    )
    assert report.texts["src/client.py"] == 'a = "v25"\nb = "v25"\n'
    assert len(report.discharged()) == 2


def test_a_second_obligation_on_a_line_an_earlier_edit_rewrote_is_satisfied_not_human() -> None:
    text = "use Google\\Ads\\GoogleAds\\V22\\Services\\GoogleAdsRow;\n"
    report = deterministic.run(
        transforms=transforms(MINIMUMS),
        requests=[
            request(text, path="src/Ads.php", line=1, obligation_id="a" * 64),
            request(
                text,
                path="src/Ads.php",
                line=1,
                claim_type="surface_reference",
                obligation_id="b" * 64,
            ),
        ],
    )
    assert [item.result for item in report.outcomes] == ["APPLIED", "SATISFIED_BY"]
    assert report.outcomes[1].reason.startswith("src/Ads.php:1 was rewritten in this run by ")
    assert len(report.discharged()) == 2
    assert report.undischarged() == ()


def test_a_reader_judged_before_the_edit_that_satisfies_it_is_settled_after_the_run() -> None:
    text = 'API_VERSION = "v22"\n'
    report = deterministic.run(
        transforms=transforms(MINIMUMS),
        requests=[
            request(text, line=1, claim_type="config_reference", obligation_id="a" * 64),
            request(text, line=1, obligation_id="b" * 64),
        ],
    )
    assert [item.result for item in report.outcomes] == ["SATISFIED_BY", "APPLIED"]
    assert report.outcomes[0].reason.startswith(
        "src/client.py:1 was rewritten in this run by version-literal"
    )
    assert report.discharged() == ("a" * 64, "b" * 64)
    assert report.texts["src/client.py"] == 'API_VERSION = "v25"\n'


def test_a_site_the_tree_already_holds_without_an_edit_is_satisfied_not_satisfied_by() -> None:
    report = deterministic.run(
        transforms=transforms(MINIMUMS), requests=[request('a = "v25"\n', line=1)]
    )
    assert [item.result for item in report.outcomes] == ["SATISFIED"]
    assert "no edit in this run" in report.outcomes[0].reason
    assert report.texts == {}


def test_a_bound_reference_is_rewritten_at_the_import_line_it_names() -> None:
    subject = request(
        "use Google\\Ads\\GoogleAds\\V22\\Services\\GoogleAdsRow;\n",
        path="src/Ads.php",
        claim_type="surface_reference",
    )
    transform = VersionLiteralTransform()
    assert transform.precondition(subject) is True
    assert (
        transform.apply(subject).text
        == "use Google\\Ads\\GoogleAds\\V25\\Services\\GoogleAdsRow;\n"
    )


def test_a_site_that_already_carries_nothing_to_rewrite_stays_open_for_a_human() -> None:
    subject = request("$request = new MutateGoogleAdsRequest();\n", claim_type="surface_reference")
    report = deterministic.run(transforms=transforms(MINIMUMS), requests=[subject])
    assert [item.result for item in report.outcomes] == ["NO_TRANSFORM"]


def test_the_runner_is_deterministic() -> None:
    requests = [
        request('a = "v22"\n', line=1, obligation_id="a" * 64),
        request('a = "v22"\n', line=1, obligation_id="b" * 64),
    ]
    first = deterministic.run(transforms=transforms(MINIMUMS), requests=requests)
    second = deterministic.run(transforms=transforms(MINIMUMS), requests=list(reversed(requests)))
    assert first.to_mapping() == second.to_mapping()

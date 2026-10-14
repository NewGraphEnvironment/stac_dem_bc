"""Every AWS write in update.yml runs on a credential fetched just before it (#60).

The OIDC session lasts one hour from the step that assumes it. The workflow
assumed it once, after setup, and run 38339265803 reached its S3 sync 49 min
later: `ExpiredToken` 11 min into the sync, 91,772 of 102,460 items uploaded,
collection.json never written.
"""

import os

import yaml

WF = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "update.yml")
CREDS = "aws-actions/configure-aws-credentials"


def steps():
    with open(WF) as fh:
        wf = yaml.safe_load(fh)
    return wf["jobs"]["update"]["steps"]


def is_creds(step):
    return str(step.get("uses", "")).startswith(CREDS + "@")


def uses_aws(step):
    run = step.get("run", "")
    return "s3_sync-ci.sh" in run or any(
        line.strip().startswith("aws ") for line in run.splitlines()
    )


def test_the_sync_is_found():
    # A rename of the sync step or its script would otherwise leave the test
    # below checking nothing.
    assert any(uses_aws(s) for s in steps())


def test_every_aws_step_is_immediately_preceded_by_a_credentials_step():
    ss = steps()
    for i, s in enumerate(ss):
        if uses_aws(s):
            prev = ss[i - 1]
            assert is_creds(prev), f"{s.get('name')!r} follows {prev.get('name') or prev.get('uses')!r}"
            # Same condition, so the credential is fetched exactly when the
            # step that needs it will run.
            assert str(prev.get("if", "")) == str(s.get("if", "")), s.get("name")


def test_every_credentials_step_assumes_the_same_role_in_the_same_region():
    creds = [s["with"] for s in steps() if is_creds(s)]
    assert len(creds) >= 2, "expected the early probe and the pre-sync re-assume"
    roles = {(c["role-to-assume"], c["aws-region"]) for c in creds}
    assert len(roles) == 1, roles

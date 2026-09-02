"""Check profile detail rendering; roll back every test row on success or failure."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import translation

from netbox_config_backup import views
from netbox_config_backup.choices import SSHHostKeyPolicyChoices
from netbox_config_backup.models import ConnectionProfile

# Catch the same mistake in any detail view using the shared field renderer.
for view in vars(views).values():
    if not isinstance(view, type) or not issubclass(view, views.ConfigObjectView):
        continue
    for field_name in view.display_fields:
        view.queryset.model._meta.get_field(field_name)

prefix = f"ncb-profile-detail-{uuid4().hex[:8]}"
with transaction.atomic():
    user = get_user_model().objects.create_superuser(username=f"{prefix}-admin")
    client = Client()
    client.force_login(user)
    cases = (
        ("ssh", True, False, SSHHostKeyPolicyChoices.STRICT),
        ("ssh", True, True, SSHHostKeyPolicyChoices.TRUST_ON_FIRST_USE),
        ("ssh", False, False, SSHHostKeyPolicyChoices.DISABLED),
        ("telnet", False, False, SSHHostKeyPolicyChoices.DISABLED),
        ("auto", True, False, SSHHostKeyPolicyChoices.STRICT),
    )
    for index, (protocol, verify, auto_trust, expected_policy) in enumerate(cases):
        profile = ConnectionProfile.objects.create(
            name=f"{prefix}-{index}",
            protocol=protocol,
            port=23 if protocol == "telnet" else 22,
            verify_host_key=verify,
            auto_trust_first_host_key=auto_trust,
        )
        response = client.get(profile.get_absolute_url())
        assert response.status_code == 200, (protocol, expected_policy, response.status_code)
        with translation.override(response.headers.get("Content-Language", "en")):
            expected_label = str(expected_policy.label)
            rows = views.ConnectionProfileView().get_extra_context(None, profile)["detail_rows"]
            assert rows[-1] == ("SSH identity verification", expected_label), rows
            assert len(rows) == 7, rows
            assert dict(rows)["protocol"] == profile.get_protocol_display(), rows
            assert dict(rows)["port"] == profile.port, rows
        assert expected_label.encode() in response.content
        assert b"SSH identity verification" in response.content
        assert profile.name.encode() in response.content
        profile.refresh_from_db()
        assert profile.host_key_policy == expected_policy

    # The detail still requires the existing view permission.
    unprivileged = get_user_model().objects.create_user(username=f"{prefix}-reader")
    client.force_login(unprivileged)
    assert client.get(profile.get_absolute_url()).status_code == 403
    transaction.set_rollback(True)

assert not ConnectionProfile.objects.filter(name__startswith=prefix).exists()
assert not get_user_model().objects.filter(username__startswith=prefix).exists()
print({"connection_profile_detail": "passed", "policy_cases": len(cases), "rolled_back": True})

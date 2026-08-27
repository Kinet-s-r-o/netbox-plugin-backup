import time

import asyncssh
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from netbox_config_backup.choices import ReceiverProtocolChoices
from netbox_config_backup.credentials import secret_provider_registry
from netbox_config_backup.credentials.base import SecretProviderError
from netbox_config_backup.models import SftpReceiverProfile
from netbox_config_backup.receiver.ftp_server import run_ftp_receiver
from netbox_config_backup.receiver.paths import receiver_profile_root
from netbox_config_backup.receiver.server import run_sftp_receiver


class Command(BaseCommand):
    help = "Run the upload-only SFTP or legacy FTP receiver for one receiver profile."

    def add_arguments(self, parser):
        parser.add_argument(
            "--profile",
            required=True,
            help="Receiver profile ID or exact name.",
        )
        parser.add_argument(
            "--wait",
            action="store_true",
            help="Wait until migrations and the selected enabled profile are available.",
        )
        parser.add_argument("--wait-interval", type=int, default=10)
        parser.add_argument(
            "--debug-protocol",
            action="store_true",
            help="Enable AsyncSSH protocol diagnostics without packet or password dumps.",
        )

    def handle(self, *args, **options):
        if options["debug_protocol"]:
            import logging

            asyncssh.set_log_level(logging.DEBUG)
            asyncssh.set_debug_level(2)
            protocol_logger = logging.getLogger("asyncssh")
            protocol_handler = logging.StreamHandler(self.stderr)
            protocol_handler.setLevel(logging.DEBUG)
            protocol_handler.setFormatter(logging.Formatter("AsyncSSH: %(message)s"))
            protocol_logger.addHandler(protocol_handler)
            protocol_logger.propagate = False
        value = options["profile"]
        profile = self._load_profile(value, wait=options["wait"], interval=options["wait_interval"])

        credential_profile = profile.credential_profile
        try:
            provider = secret_provider_registry.get(credential_profile.provider_id)
            credentials = provider.resolve(credential_profile.secret_reference)
        except (LookupError, SecretProviderError) as exc:
            raise CommandError("Receiver credential resolution failed.") from exc

        plugin_settings = settings.PLUGINS_CONFIG["netbox_config_backup"]
        root = receiver_profile_root(plugin_settings["receiver_root"], profile.pk)
        protocol_label = profile.get_protocol_display()
        self.stdout.write(
            f"Starting Config Backup {protocol_label} receiver {profile.name!r} on "
            f"{profile.listen_host}:{profile.listen_port}."
        )
        try:
            if profile.protocol == ReceiverProtocolChoices.FTP:
                run_ftp_receiver(
                    listen_host=profile.listen_host,
                    listen_port=profile.listen_port,
                    advertised_host=profile.advertised_host,
                    profile_root=root,
                    upload_directory=profile.upload_directory,
                    passive_port_start=profile.passive_port_start,
                    passive_port_end=profile.passive_port_end,
                    credentials=credentials,
                )
            else:
                run_sftp_receiver(
                    listen_host=profile.listen_host,
                    listen_port=profile.listen_port,
                    profile_root=root,
                    upload_directory=profile.upload_directory,
                    host_key_paths=(
                        plugin_settings["receiver_host_key_path"],
                        plugin_settings["receiver_rsa_host_key_path"],
                    ),
                    credentials=credentials,
                )
        except (OSError, ValueError, asyncssh.Error) as exc:
            raise CommandError(str(exc)) from exc

    def _load_profile(self, value, *, wait: bool, interval: int):
        while True:
            try:
                query = SftpReceiverProfile.objects.select_related("credential_profile")
                profile = (
                    query.filter(pk=int(value)).first()
                    if value.isdecimal()
                    else query.filter(name=value).first()
                )
            except DatabaseError:
                profile = None
            if profile is not None and profile.enabled:
                return profile
            if not wait:
                if profile is None:
                    raise CommandError(
                        f"Device upload receiver {value!r} does not exist."
                    )
                raise CommandError(
                    f"Device upload receiver {profile.name!r} is disabled."
                )
            self.stdout.write(f"Waiting for enabled device upload receiver {value!r}...")
            time.sleep(max(1, interval))

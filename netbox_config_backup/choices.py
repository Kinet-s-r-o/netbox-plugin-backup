from django.db import models
from django.utils.translation import gettext_lazy as _


class InterfaceLanguageChoices(models.TextChoices):
    ENGLISH = "en", _("English")
    SLOVAK = "sk", _("Slovenčina")


class ScheduleTypeChoices(models.TextChoices):
    INTERVAL = "interval", _("Interval")
    DAILY = "daily", _("Daily")


class StoreModeChoices(models.TextChoices):
    CHANGED_ONLY = "changed_only", _("Changed configurations only")
    EVERY_SUCCESS = "every_success", _("Every successful collection")


class AddressPreferenceChoices(models.TextChoices):
    OOB_FIRST = "oob_first", _("Dedicated management IP (OOB) first")
    PRIMARY4_FIRST = "primary4_first", _("Primary IPv4 first")
    PRIMARY6_FIRST = "primary6_first", _("Primary IPv6 first")


class ConnectionProtocolChoices(models.TextChoices):
    AUTOMATIC = "auto", _("Automatic from driver and port")
    SSH = "ssh", "SSH"
    TELNET = "telnet", "Telnet"


class SSHHostKeyPolicyChoices(models.TextChoices):
    STRICT = "strict", _("Require manual approval")
    TRUST_ON_FIRST_USE = "tofu", _("Trust first key automatically")
    DISABLED = "disabled", _("Do not verify SSH identity")


class AuthTypeChoices(models.TextChoices):
    PASSWORD = "password", _("Password")
    SSH_KEY = "ssh_key", _("SSH private key")


class ReceiverModeChoices(models.TextChoices):
    DIRECT = "direct", _("Direct from device")
    REVERSE_TUNNEL = "reverse_tunnel", _("Reverse SSH tunnel")


class ReceiverProtocolChoices(models.TextChoices):
    SFTP = "sftp", _("SFTP (recommended)")
    FTP = "ftp", _("Legacy FTP (ALFOplus only)")


class DestinationProtocolChoices(models.TextChoices):
    LOCAL = "local", _("Local (primary storage)")
    SFTP = "sftp", _("SFTP (recommended, encrypted)")
    FTP = "ftp", _("FTP (unencrypted)")
    NFS = "nfs", _("NFS mount")
    SMB = "smb", _("SMB3 / Samba mount")


# SFTP remains readable for installations upgraded from an older plugin
# version, but new storage profiles deliberately expose only the currently
# supported secondary-storage implementations below.
MANAGED_DESTINATION_PROTOCOLS = (
    DestinationProtocolChoices.LOCAL,
    DestinationProtocolChoices.FTP,
    DestinationProtocolChoices.NFS,
    DestinationProtocolChoices.SMB,
)
REPLICATED_DESTINATION_PROTOCOLS = (
    DestinationProtocolChoices.FTP,
    DestinationProtocolChoices.NFS,
    DestinationProtocolChoices.SMB,
)
MOUNTED_DESTINATION_PROTOCOLS = (
    DestinationProtocolChoices.NFS,
    DestinationProtocolChoices.SMB,
)


class FtpAuditFrequencyChoices(models.TextChoices):
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")


class FtpAuditStatusChoices(models.TextChoices):
    HEALTHY = "healthy", _("Healthy")
    PROBLEMS = "problems", _("Problems found")
    FAILED = "failed", _("Failed")


class WeekdayChoices(models.IntegerChoices):
    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class SSHHostKeyStatusChoices(models.TextChoices):
    PENDING = "pending", _("Pending approval")
    TRUSTED = "trusted", _("Trusted")
    REJECTED = "rejected", _("Rejected")


class ReplicaStatusChoices(models.TextChoices):
    PENDING = "pending", _("Pending")
    QUEUED = "queued", _("Queued")
    RUNNING = "running", _("Running")
    SUCCESS = "success", _("Successful")
    FAILED = "failed", _("Failed")


class TargetStatusChoices(models.TextChoices):
    NEVER = "never", _("Never backed up")
    HEALTHY = "healthy", _("Healthy")
    FAILED = "failed", _("Failed")
    STALE = "stale", _("Stale")
    DISABLED = "disabled", _("Disabled")


class RunSourceChoices(models.TextChoices):
    SCHEDULED = "scheduled", _("Scheduled")
    MANUAL = "manual", _("Manual")
    RETRY = "retry", _("Retry")
    PRE_CHANGE = "pre_change", _("Pre-change")


class RunStatusChoices(models.TextChoices):
    QUEUED = "queued", _("Queued")
    RUNNING = "running", _("Running")
    SUCCESS_UNCHANGED = "success_unchanged", _("Success (unchanged)")
    SUCCESS_CHANGED = "success_changed", _("Success (changed)")
    PARTIAL = "partial", _("Partial")
    FAILED = "failed", _("Failed")
    ERRORED = "errored", _("Errored")
    SKIPPED = "skipped", _("Skipped")

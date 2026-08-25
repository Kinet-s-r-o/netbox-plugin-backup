from django.db import models


class ScheduleTypeChoices(models.TextChoices):
    INTERVAL = "interval", "Interval"
    DAILY = "daily", "Daily"


class StoreModeChoices(models.TextChoices):
    CHANGED_ONLY = "changed_only", "Changed configurations only"
    EVERY_SUCCESS = "every_success", "Every successful collection"


class AddressPreferenceChoices(models.TextChoices):
    OOB_FIRST = "oob_first", "OOB first"
    PRIMARY4_FIRST = "primary4_first", "Primary IPv4 first"
    PRIMARY6_FIRST = "primary6_first", "Primary IPv6 first"


class ConnectionProtocolChoices(models.TextChoices):
    AUTOMATIC = "auto", "Automatic from driver and port"
    SSH = "ssh", "SSH"
    TELNET = "telnet", "Telnet"


class AuthTypeChoices(models.TextChoices):
    PASSWORD = "password", "Password"
    SSH_KEY = "ssh_key", "SSH private key"


class ReceiverModeChoices(models.TextChoices):
    DIRECT = "direct", "Direct from device"
    REVERSE_TUNNEL = "reverse_tunnel", "Reverse SSH tunnel"


class ReceiverProtocolChoices(models.TextChoices):
    SFTP = "sftp", "SFTP (recommended)"
    FTP = "ftp", "Legacy FTP (ALFOplus only)"


class DestinationProtocolChoices(models.TextChoices):
    SFTP = "sftp", "SFTP (recommended, encrypted)"
    FTP = "ftp", "FTP (unencrypted)"


class FtpAuditFrequencyChoices(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"


class FtpAuditStatusChoices(models.TextChoices):
    HEALTHY = "healthy", "Healthy"
    PROBLEMS = "problems", "Problems found"
    FAILED = "failed", "Failed"


class WeekdayChoices(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class SSHHostKeyStatusChoices(models.TextChoices):
    PENDING = "pending", "Pending approval"
    TRUSTED = "trusted", "Trusted"
    REJECTED = "rejected", "Rejected"


class ReplicaStatusChoices(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Successful"
    FAILED = "failed", "Failed"


class TargetStatusChoices(models.TextChoices):
    NEVER = "never", "Never backed up"
    HEALTHY = "healthy", "Healthy"
    FAILED = "failed", "Failed"
    STALE = "stale", "Stale"
    DISABLED = "disabled", "Disabled"


class RunSourceChoices(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    MANUAL = "manual", "Manual"
    RETRY = "retry", "Retry"
    PRE_CHANGE = "pre_change", "Pre-change"


class RunStatusChoices(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCESS_UNCHANGED = "success_unchanged", "Success (unchanged)"
    SUCCESS_CHANGED = "success_changed", "Success (changed)"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"
    ERRORED = "errored", "Errored"
    SKIPPED = "skipped", "Skipped"

from django.utils.translation import gettext, gettext_noop
from django.utils.translation import gettext_lazy as _
from netbox.choices import ButtonColorChoices
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

from netbox_config_backup.services.attention import current_attention

# Plain text on purpose: NetBox also uses the menu label as the heading's aria-label.
ATTENTION_MARK = "\N{HEAVY EXCLAMATION MARK SYMBOL}"


class ConfigBackupMenu(PluginMenu):
    """Top-level menu whose heading carries a red exclamation mark while something failed.

    NetBox renders ``menu.label`` on every request, so the label is computed on access.
    """

    @property
    def label(self):
        label = str(self._label)
        return f"{label} {ATTENTION_MARK}" if current_attention().total else label

    @label.setter
    def label(self, value):
        self._label = value


class AttentionButtons:
    """Menu item buttons that exist only while ``field`` of the attention state is non-zero.

    NetBox iterates ``item.buttons`` on every request, so a plain iterable is enough to
    make the red marker appear and disappear without a restart.
    """

    def __init__(self, *, link, field, title, query=""):
        self._link = link
        self._field = field
        self._title = title
        self._query = query

    def __iter__(self):
        count = getattr(current_attention(), self._field)
        if not count:
            return iter(())
        button = PluginMenuButton(
            link=self._link,
            title=gettext(self._title) % {"count": count},
            icon_class="mdi mdi-alert-circle",
            color=ButtonColorChoices.RED,
        )
        if self._query:
            button.url = f"{button.url}?{self._query}"
        return iter((button,))


def _item(link, link_text, permission, *, attention=None):
    item = PluginMenuItem(link=link, link_text=link_text, permissions=(permission,))
    if attention is not None:
        # PluginMenuItem only accepts a list or tuple; the dynamic iterable is attached after.
        item.buttons = AttentionButtons(link=link, **attention)
    return item


menu = ConfigBackupMenu(
    label=_("Config Backup"),
    groups=(
        (
            _("Backups"),
            (
                _item(
                    "plugins:netbox_config_backup:home",
                    _("Overview"),
                    "netbox_config_backup.view_backuptarget",
                ),
                _item(
                    "plugins:netbox_config_backup:backuptarget_list",
                    _("Devices"),
                    "netbox_config_backup.view_backuptarget",
                    attention={
                        "field": "failed_targets",
                        "title": gettext_noop("Failed devices: %(count)d"),
                        "query": "status=failed",
                    },
                ),
                _item(
                    "plugins:netbox_config_backup:backupdestination_list",
                    _("Storages"),
                    "netbox_config_backup.view_backupdestination",
                    attention={
                        "field": "storages",
                        "title": gettext_noop("Storages needing attention: %(count)d"),
                    },
                ),
                _item(
                    "plugins:netbox_config_backup:backuprun_list",
                    _("Runs"),
                    "netbox_config_backup.view_backuprun",
                    attention={
                        "field": "stuck_runs",
                        "title": gettext_noop("Stuck runs: %(count)d"),
                    },
                ),
                _item(
                    "plugins:netbox_config_backup:configrevision_list",
                    _("Revisions"),
                    "netbox_config_backup.view_configrevision",
                ),
            ),
        ),
        (
            _("Configuration"),
            (
                _item(
                    "plugins:netbox_config_backup:advanced_settings",
                    _("Settings"),
                    "netbox_config_backup.view_operationalsettings",
                ),
                _item(
                    "plugins:netbox_config_backup:help",
                    _("Help"),
                    "netbox_config_backup.view_backuptarget",
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-content-save-cog-outline",
)

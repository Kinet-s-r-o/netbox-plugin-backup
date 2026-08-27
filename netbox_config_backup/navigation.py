from django.utils.translation import gettext_lazy as _
from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label=_("Config Backup"),
    groups=(
        (
            _("Backups"),
            (
                PluginMenuItem(
                    link="plugins:netbox_config_backup:home",
                    link_text=_("Overview"),
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backuptarget_list",
                    link_text=_("Devices"),
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backupdestination_list",
                    link_text=_("Storages"),
                    permissions=("netbox_config_backup.view_backupdestination",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backuprun_list",
                    link_text=_("Runs"),
                    permissions=("netbox_config_backup.view_backuprun",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:configrevision_list",
                    link_text=_("Revisions"),
                    permissions=("netbox_config_backup.view_configrevision",),
                ),
            ),
        ),
        (
            _("Configuration"),
            (
                PluginMenuItem(
                    link="plugins:netbox_config_backup:advanced_settings",
                    link_text=_("Settings"),
                    permissions=("netbox_config_backup.view_operationalsettings",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:help",
                    link_text=_("Help"),
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-content-save-cog-outline",
)

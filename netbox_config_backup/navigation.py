from netbox.plugins import PluginMenu, PluginMenuItem

menu = PluginMenu(
    label="Config Backup",
    groups=(
        (
            "Backups",
            (
                PluginMenuItem(
                    link="plugins:netbox_config_backup:home",
                    link_text="Overview",
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backuptarget_list",
                    link_text="Devices",
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backupdestination_list",
                    link_text="Storages",
                    permissions=("netbox_config_backup.view_backupdestination",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:backuprun_list",
                    link_text="Runs",
                    permissions=("netbox_config_backup.view_backuprun",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:configrevision_list",
                    link_text="Revisions",
                    permissions=("netbox_config_backup.view_configrevision",),
                ),
            ),
        ),
        (
            "Configuration",
            (
                PluginMenuItem(
                    link="plugins:netbox_config_backup:advanced_settings",
                    link_text="Settings",
                    permissions=("netbox_config_backup.view_operationalsettings",),
                ),
                PluginMenuItem(
                    link="plugins:netbox_config_backup:help",
                    link_text="Help",
                    permissions=("netbox_config_backup.view_backuptarget",),
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-content-save-cog-outline",
)

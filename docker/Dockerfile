ARG NETBOX_IMAGE=netbox-zabbix-status:dev
FROM ${NETBOX_IMAGE}

COPY . /opt/netbox-plugin-backup

RUN /usr/local/bin/uv pip install --no-cache \
    --python /opt/netbox/venv/bin/python \
    --editable /opt/netbox-plugin-backup \
    && install -d -o netbox -g root -m 0700 /var/lib/netbox-config-backup

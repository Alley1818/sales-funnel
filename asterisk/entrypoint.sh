#!/bin/bash
# Copy our configs into /etc/asterisk (which is a Docker volume)
# This runs at container start, after the volume is mounted
cp /tmp/asterisk-conf/* /etc/asterisk/
chown asterisk:asterisk /etc/asterisk/*.conf
chmod 644 /etc/asterisk/*.conf

# Start Asterisk in foreground
exec asterisk -vvvdddf -T -W -U asterisk -p

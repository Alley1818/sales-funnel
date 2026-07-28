FROM andrius/asterisk:latest

# Copy our configs to a staging area (VOLUME /etc/asterisk can't be overwritten in build)
RUN mkdir -p /tmp/asterisk-conf
COPY pjsip.conf /tmp/asterisk-conf/
COPY extensions.conf /tmp/asterisk-conf/
COPY modules.conf /tmp/asterisk-conf/
COPY http.conf /tmp/asterisk-conf/
COPY ari.conf /tmp/asterisk-conf/
COPY websocket_client.conf /tmp/asterisk-conf/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

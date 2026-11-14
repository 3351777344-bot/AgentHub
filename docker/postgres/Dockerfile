FROM pgvector/pgvector:0.8.7-pg17-bookworm@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8 AS vector
FROM postgres:17-bookworm@sha256:40865bc877a289da75963d97d72b59633f639fc5af7a4e856bfa3714cae5b536 AS prepared

COPY --from=vector /usr/lib/postgresql/17/lib/vector.so /usr/lib/postgresql/17/lib/
COPY --from=vector /usr/share/postgresql/17/extension/vector* /usr/share/postgresql/17/extension/
# Upstream uses gosu once to drop privileges. Reuse the distribution's setpriv.
RUN sed -i 's/exec gosu postgres /exec setpriv --reuid=postgres --regid=postgres --init-groups /' /usr/local/bin/docker-entrypoint.sh \
    && grep -q 'exec setpriv --reuid=postgres' /usr/local/bin/docker-entrypoint.sh \
    && rm /usr/local/bin/gosu

# Copy the merged filesystem so the superseded executable is absent from every layer.
FROM scratch
COPY --from=prepared / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/17/bin \
    LANG=en_US.utf8 PG_MAJOR=17 PG_VERSION=17.12-1.pgdg12+1 \
    PGDATA=/var/lib/postgresql/data
VOLUME ["/var/lib/postgresql/data"]
EXPOSE 5432
STOPSIGNAL SIGINT
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["postgres"]

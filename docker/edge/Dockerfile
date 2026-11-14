FROM nginx:stable-bookworm@sha256:552e7481ca93ffccd046aa658dbbed22caefbc09c66fa7cd247cbb90b8a5c609

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=0 update \
    && apt-get -o Acquire::Retries=0 upgrade --yes \
    && rm -rf /var/lib/apt/lists/*
COPY nginx.conf.template /etc/nginx/agenthub.conf.template
COPY start.sh /usr/local/bin/agenthub-edge
ENTRYPOINT ["sh", "/usr/local/bin/agenthub-edge"]

FROM nixos/nix:2.20.5 AS tool-base

RUN echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf

COPY flake.nix /opt/pbkat/flake.nix
COPY flake.lock /opt/pbkat/flake.lock
COPY package.yaml /opt/pbkat/package.yaml

WORKDIR /opt/pbkat

RUN nix develop

RUN nix develop --command cabal user-config init

ENV LANG=C.UTF-8

RUN echo "[safe] \
    directory = /opt/pbkat" > ~/.gitconfig

ENTRYPOINT ["nix", "develop"]

FROM tool-base AS backend

ENTRYPOINT []

WORKDIR /opt/pbkat

COPY . /opt/pbkat


RUN nix develop --command nix-shell -p nodejs_20 --run \
    "cd /opt/pbkat && npm --prefix editor-webserver ci"

RUN nix develop --command nix-shell -p nodejs_20 --run \
    "cd /opt/pbkat && npm --prefix editor-webserver run build"

ENV NODE_ENV=production
ENV PORT=8080

EXPOSE 8080

CMD ["nix", "develop", "--command", "nix-shell", "-p", "nodejs_20", "--run", "cd /opt/pbkat && npm --prefix editor-webserver run start"]

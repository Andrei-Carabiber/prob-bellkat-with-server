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

# --- new stage: build only the Haskell dependency set ---
FROM tool-base AS backend-deps

WORKDIR /opt/pbkat

RUN nix develop --command sh -c \
    "cd /opt/pbkat && hpack"

# Only builds deps, not your own modules — cached unless package.yaml/flake changes
RUN nix develop --command sh -c \
    "cd /opt/pbkat && cabal build --builddir=/opt/pbkat/shared-build-cache --only-dependencies"

# --- new stage: cache npm deps separately from source ---
FROM backend-deps AS backend-npm-deps

COPY editor-webserver/package.json editor-webserver/package-lock.json /opt/pbkat/editor-webserver/

RUN nix develop --command nix-shell -p nodejs_20 --run \
    "cd /opt/pbkat && npm --prefix editor-webserver ci"

# --- final stage: bring in full source, build only what changed ---
FROM backend-npm-deps AS backend

ENTRYPOINT []

WORKDIR /opt/pbkat

COPY . /opt/pbkat

RUN nix develop --command sh -c \
    "cd /opt/pbkat && hpack"

RUN nix develop --command sh -c \
    "cd /opt/pbkat && cabal build --builddir=/opt/pbkat/shared-build-cache"

RUN nix develop --command nix-shell -p nodejs_20 --run \
    "cd /opt/pbkat && npm --prefix editor-webserver run build"

ENV NODE_ENV=production
ENV PORT=8080

EXPOSE 8080

CMD ["nix", "develop", "--command", "nix-shell", "-p", "nodejs_20", "--run", "cd /opt/pbkat && npm --prefix editor-webserver run start"]
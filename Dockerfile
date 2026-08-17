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

# --- Haskell deps only, cached until package.yaml/flake changes ---
FROM tool-base AS backend-deps

WORKDIR /opt/pbkat

RUN nix develop --command sh -c \
    "cd /opt/pbkat && hpack && cabal build --builddir=/opt/pbkat/shared-build-cache --only-dependencies"

# --- npm deps only, cached until package.json/lock changes ---
FROM backend-deps AS backend-npm-deps

COPY editor-webserver/package.json editor-webserver/package-lock.json /opt/pbkat/editor-webserver/

RUN nix develop --command sh -c \
    "cd /opt/pbkat && npm --prefix editor-webserver ci"

# --- Haskell source: only rebuilds when src/ (Haskell) changes ---
FROM backend-npm-deps AS backend-hs-build

WORKDIR /opt/pbkat

COPY src ./src
COPY playground-example ./playground-example
COPY examples ./examples
COPY probabilistic-examples ./probabilistic-examples
COPY quantum-examples ./quantum-examples
COPY test ./test

RUN nix develop --command sh -c \
    "cd /opt/pbkat && hpack && cabal build --builddir=/opt/pbkat/shared-build-cache"

# --- Node source: only rebuilds when editor-webserver/ changes ---
FROM backend-hs-build AS backend

ENTRYPOINT []

WORKDIR /opt/pbkat

COPY editor-webserver ./editor-webserver

RUN nix develop --command sh -c \
    "cd /opt/pbkat && npm --prefix editor-webserver run build"

ENV NODE_ENV=production
ENV PORT=8080

EXPOSE 8080

CMD ["nix", "develop", "--command", "sh", "-c", \
    "cd /opt/pbkat && npm --prefix editor-webserver run start"]
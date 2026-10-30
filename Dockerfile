# syntax=docker/dockerfile:1.7
ARG AWG_GO_COMMIT=b5928efb6ca19f0153958460c3d141f04abc5c2e
ARG AWG_TOOLS_COMMIT=ee0f0a9aa34ff0a0da4b3433b9512781cfe02843
FROM --platform=linux/amd64 golang:1.27.3-alpine3.23@sha256:8a5046497cda39860b832ffdbe8dd66018a0f05bb01352f533a36e7e2ced66a0 AS awg-go
ARG AWG_GO_COMMIT
RUN apk add --no-cache build-base git
RUN git clone --filter=blob:none https://github.com/amnezia-vpn/amneziawg-go.git /src && git -C /src checkout --detach "$AWG_GO_COMMIT" && test "$(git -C /src rev-parse HEAD)" = "$AWG_GO_COMMIT"
WORKDIR /src
COPY scripts/engine-dependencies/awg/go.mod scripts/engine-dependencies/awg/go.sum ./
RUN export GOTOOLCHAIN=local && go mod download && go mod verify && CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-linkmode external -extldflags "-static" -s -w' -o /out/amneziawg-go .
FROM --platform=linux/amd64 alpine:3.23.7@sha256:ff7f0c727dd796aa36787bfcd9a64bf35a6ce72a73451b3359fde849b6703d63 AS awg-tools
ARG AWG_TOOLS_COMMIT
RUN apk add --no-cache build-base git linux-headers
RUN git clone --filter=blob:none https://github.com/amnezia-vpn/amneziawg-tools.git /src && git -C /src checkout --detach "$AWG_TOOLS_COMMIT" && test "$(git -C /src rev-parse HEAD)" = "$AWG_TOOLS_COMMIT" && make -C /src/src WITH_SYSTEMDUNITS=no WITH_BASHCOMPLETION=no
RUN install -Dm755 /src/src/wg /out/awg && install -Dm755 /src/src/wg-quick/linux.bash /out/awg-quick
FROM --platform=linux/amd64 golang:1.27.3-alpine3.23@sha256:8a5046497cda39860b832ffdbe8dd66018a0f05bb01352f533a36e7e2ced66a0 AS wireguard-go
RUN apk add --no-cache git
RUN git clone https://git.zx2c4.com/wireguard-go /src && git -C /src checkout --detach f333402bd9cbe0f3eeb02507bd14e23d7d639280 && test "$(git -C /src rev-parse HEAD)" = f333402bd9cbe0f3eeb02507bd14e23d7d639280
WORKDIR /src
COPY scripts/engine-dependencies/wg/go.mod scripts/engine-dependencies/wg/go.sum ./
RUN export GOTOOLCHAIN=local && go mod download && go mod verify && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/wireguard-go .
FROM --platform=linux/amd64 alpine:3.23.7@sha256:ff7f0c727dd796aa36787bfcd9a64bf35a6ce72a73451b3359fde849b6703d63 AS wireguard-tools
RUN apk add --no-cache build-base git linux-headers
RUN git clone https://git.zx2c4.com/wireguard-tools /src && git -C /src checkout --detach e2ecaaa739144997ccff89d6ad6ec81698ea6ced && test "$(git -C /src rev-parse HEAD)" = e2ecaaa739144997ccff89d6ad6ec81698ea6ced && make -C /src/src WITH_SYSTEMDUNITS=no WITH_BASHCOMPLETION=no
RUN install -Dm755 /src/src/wg /out/wg && install -Dm755 /src/src/wg-quick/linux.bash /out/wg-quick
FROM --platform=linux/amd64 node:22.23.4-alpine3.23@sha256:6abbcf5dd6b231da109bcde1181db1d92bf78015ae1a9514d9807c1f857869db AS node-deps
WORKDIR /app
COPY src/package.json src/pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate && pnpm install --prod --frozen-lockfile
FROM --platform=linux/amd64 alpine:3.23.7@sha256:ff7f0c727dd796aa36787bfcd9a64bf35a6ce72a73451b3359fde849b6703d63 AS runtime-common
LABEL org.opencontainers.image.title="wg-dual-easy" org.opencontainers.image.source="https://github.com/a-baader/wg-dual-easy" org.opencontainers.image.licenses="CC-BY-NC-SA-4.0"
RUN apk add --no-cache bash ca-certificates dumb-init iproute2 nftables libstdc++
COPY --from=node-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=node-deps /usr/local/LICENSE /usr/local/share/licenses/node/LICENSE
WORKDIR /app
COPY src/lib/ ./lib/
COPY src/www/ ./www/
COPY src/*.js src/package.json ./
COPY --from=node-deps /app/node_modules ./node_modules
ENV NODE_ENV=production
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=8s --start-period=15s --retries=3 CMD ["node", "/app/healthcheck.js"]
ENTRYPOINT ["/usr/bin/dumb-init", "--", "node", "/app/server.js"]
CMD ["serve"]

FROM runtime-common AS wg
COPY --from=wireguard-go /out/wireguard-go /usr/local/bin/wireguard-go
COPY --from=wireguard-tools /out/wg /usr/local/bin/wg
COPY --from=wireguard-tools /out/wg-quick /usr/local/bin/wg-quick
COPY --from=wireguard-go /src/LICENSE /usr/local/share/licenses/wireguard-go/LICENSE
COPY --from=wireguard-tools /src/COPYING /usr/local/share/licenses/wireguard-tools/COPYING
RUN printf 'wireguard-go=f333402bd9cbe0f3eeb02507bd14e23d7d639280\nwireguard-tools=e2ecaaa739144997ccff89d6ad6ec81698ea6ced\n' > /usr/local/share/vpn-build-revisions
ENV VPN_BACKEND=wireguard VPN_DATA_DIR=/data VPN_RUNTIME_DIR=/run/wg-easy-dual WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go

FROM runtime-common AS awg-runtime
ARG AWG_GO_COMMIT
ARG AWG_TOOLS_COMMIT
LABEL org.opencontainers.image.awg-go-revision="$AWG_GO_COMMIT" org.opencontainers.image.awg-tools-revision="$AWG_TOOLS_COMMIT"
COPY --from=awg-go /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools /out/awg /usr/local/bin/awg
COPY --from=awg-tools /out/awg-quick /usr/local/bin/awg-quick
RUN ln -s /usr/local/bin/awg /usr/local/bin/wg && ln -s /usr/local/bin/awg-quick /usr/local/bin/wg-quick && printf 'awg-go=%s\nawg-tools=%s\n' "$AWG_GO_COMMIT" "$AWG_TOOLS_COMMIT" > /usr/local/share/awg-build-revisions
ENV VPN_BACKEND=amneziawg AWG_EASY_DATA_DIR=/data AWG_EASY_RUNTIME_DIR=/run/awg-easy-3 WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go

# Тестовые helper и fixtures доступны только в явных test target.
FROM wg AS wg-test
COPY src/test/ /app/test/

FROM awg-runtime AS awg-test
COPY src/test/ /app/test/

# Сборка по умолчанию остаётся production AWG без тестов.
FROM awg-runtime AS awg

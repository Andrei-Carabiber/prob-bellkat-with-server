MODE = cabal

ifeq ($(MODE),cabal)
	build_and_run = cabal build $(1) && cabal run $(1) --
else ifeq ($(MODE),stack)
	build_and_run = stack build && stack run $(1) --
else ifeq ($(MODE),docker)
	build_and_run = docker run --rm -i pbkat:latest $(1)
else ifeq ($(MODE),direct)
	build_and_run = $(1)
endif

QUANTUM_PROTOS = \
	Pa \
	Pd \
	Pd_parallel \
	P5_3_pompili \
	P5_ASAP \
	P5_Li \
	P5_Star

clean:
	rm -rv output

all-quant: $(QUANTUM_PROTOS:%=output/quantum-examples/%.quant) 

output/quantum-examples/%.quant: output/quantum-examples/%.json
	$(call build_and_run,quant$(basename $(notdir $<))) \
		--json probability \
		>$@ <$<

output/quantum-examples/%.json: quantum-examples/%.hs
	mkdir -p $(dir $@)
	$(call build_and_run,quant$(basename $(notdir $<))) \
		+RTS --machine-readable -t -RTS --json run \
		>$@ \
		2>$@.stderr

output/examples/%.json: examples/%.hs
	mkdir -p $(dir $@)
	$(call build_and_run,$(basename $(notdir $<))) \
		+RTS --machine-readable -t -RTS \
		>$@ \
		2>$@.stderr

output/quantum-examples/%.txt: quantum-examples/%.hs
	mkdir -p $(dir $@)
	$(call build_and_run,quant$(basename $(notdir $<))) \
		+RTS --machine-readable -t -RTS run \
		>$@ \
		2>$@.stderr

.PHONY: test
test:

README.pdf: README.md metadata.yaml
	pandoc --pdf-engine=lualatex --metadata-file metadata.yaml --output $@ $<



# QBKAT Interface dev commands

NETWORK := pbkat-dev
REDIS_CONTAINER := pbkat-redis

.PHONY: dev-webserver dev-down dev-network dev-redis

dev-webserver: dev-network dev-redis
	docker run --rm -it \
	   --entrypoint nix \
	   --network $(NETWORK) \
	   -p 8080:8080 \
	   -e REDIS_URL=redis://$(REDIS_CONTAINER):6379 \
	   --mount type=bind,source=$(PWD),target=/opt/pbkat \
	   pbkat:latest \
	   develop --command nix-shell -p nodejs_20 --run 'cd /opt/pbkat && npm run dev --prefix editor-webserver'

dev-network:
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

dev-redis: dev-network
	@if [ -z "$$(docker ps -q -f name=^/$(REDIS_CONTAINER)$$)" ]; then \
		if [ -n "$$(docker ps -aq -f name=^/$(REDIS_CONTAINER)$$)" ]; then \
			echo "Starting existing redis container..."; \
			docker start $(REDIS_CONTAINER) >/dev/null; \
		else \
			echo "Creating redis container..."; \
			docker run -d --name $(REDIS_CONTAINER) \
				--network $(NETWORK) \
				-p 6379:6379 \
				redis:alpine >/dev/null; \
		fi \
	fi

dev-down:
	-docker stop $(REDIS_CONTAINER)
	-docker rm $(REDIS_CONTAINER)
	-docker network rm $(NETWORK)
.PHONY: help
help:
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

#
# If you want to see the full commands, run:
#   NOISY_BUILD=y make
#
ifeq ($(NOISY_BUILD),)
    ECHO_PREFIX=@
    CMD_PREFIX=@
else
    ECHO_PREFIX=@\#
    CMD_PREFIX=    PIPE_DEV_NULL=
endif

TAG=$(shell git rev-parse HEAD)

docling-serve-cpu-image: Containerfile ## Build docling-serve "cpu only" continaer image
	$(ECHO_PREFIX) printf "  %-12s Containerfile\n" "[docling-serve CPU ONLY]"
	$(CMD_PREFIX) docker build --build-arg CPU_ONLY=true -f Containerfile --platform linux/amd64 -t ghcr.io/ds4sd/docling-serve/cpu:$(TAG) .
	$(CMD_PREFIX) docker tag ghcr.io/ds4sd/docling-serve/cpu:$(TAG) ghcr.io/ds4sd/docling-serve/cpu:main
	$(CMD_PREFIX) docker tag ghcr.io/ds4sd/docling-serve/cpu:$(TAG) quay.io/ds4sd/docling-serve/cpu:main

docling-serve-gpu-image: Containerfile ## Build docling-serve continaer image with GPU support
	$(ECHO_PREFIX) printf "  %-12s Containerfile\n" "[docling-serve with GPU]"
	$(CMD_PREFIX) docker build --build-arg CPU_ONLY=false -f Containerfile --platform linux/amd64 -t ghcr.io/ds4sd/docling-serve/gpu:$(TAG) .
	$(CMD_PREFIX) docker tag ghcr.io/ds4sd/docling-serve/gpu:$(TAG) ghcr.io/ds4sd/docling-serve/gpu:main
	$(CMD_PREFIX) docker tag ghcr.io/ds4sd/docling-serve/gpu:$(TAG) quay.io/ds4sd/docling-serve/gpu:main

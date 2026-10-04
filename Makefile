.PHONY: build up down certs verify security-scan
COMPOSE := docker compose -f infra/docker-compose.prod.yml

certs:
	bash infra/nginx/gen-self-signed-cert.sh

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

verify:
	bash infra/scripts/verify.sh

# Build the API image, then fail if Trivy finds any CRITICAL vulnerability.
# --ignore-unfixed skips CVEs with no available patch (remove it for a stricter gate).
security-scan:
	$(COMPOSE) build api
	trivy image --severity CRITICAL --exit-code 1 --ignore-unfixed neuroflow-api:latest

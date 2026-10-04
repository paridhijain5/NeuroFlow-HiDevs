# Production stack

```
internet -> nginx (80/443, TLS, gzip, 60 r/m rate limit, security headers)
              |-- /api/*  -> api x2 (non-root, read-only fs, cap_drop ALL)  -> postgres, redis (internal network)
              |-- /       -> frontend (Next.js standalone, non-root)
worker x2 consume jobs from redis
```

## Run
```bash
cp infra/.env.prod.example infra/.env        # edit secrets
bash infra/nginx/gen-self-signed-cert.sh     # dev cert (gitignored)
docker compose -f infra/docker-compose.prod.yml up --build -d
bash infra/scripts/verify.sh                 # checks the Task 15 checklist
```
Open https://localhost (accept the self-signed warning). API: https://localhost/api/health

## Swap the self-signed cert for Let's Encrypt
1. Point your domain's DNS at the server; set `server_name your.domain;` in `nginx.conf`.
2. Get a cert (webroot method), e.g.:
   `docker run --rm -v ./nginx/letsencrypt:/etc/letsencrypt -v ./nginx/certbot:/var/www/certbot certbot/certbot certonly --webroot -w /var/www/certbot -d your.domain`
3. In `docker-compose.prod.yml` add to the nginx service volumes:
   `./nginx/letsencrypt:/etc/letsencrypt:ro` and `./nginx/certbot:/var/www/certbot:ro`
4. In `nginx.conf` change the two paths to
   `/etc/letsencrypt/live/your.domain/fullchain.pem` and `.../privkey.pem`, raise HSTS `max-age` to 31536000.
5. `docker compose -f infra/docker-compose.prod.yml restart nginx`. Renew with `certbot renew` + `nginx -s reload` on a cron.

## Notes
- Containers are named `neuroflow-api-1/2` because the compose file sets `name: neuroflow`.
- If you recreate `api`, run `docker compose ... restart nginx` (upstream IPs are resolved at nginx start).
- Frontend needs `output: "standalone"` in `next.config.js`.
- `X-Upstream-Addr` response header exposes the replica's internal address; remove it in `nginx.conf` if undesired.

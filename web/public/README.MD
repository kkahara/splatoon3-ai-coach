The public site is not running locally right now. Docker is stopped, and nothing is listening on port 8770 or port 5173. Your last `docker compose up` failed because the Docker daemon was not running.

**Port 8770 is the real site.** The container runs `s3-coach public-site` and serves the built React app from `web/public/dist`. Locally, Docker Compose publishes that as `http://127.0.0.1:8770`. Production uses the same image. Compose also attaches the app to the external `textroute_web` network so the reverse proxy can reach it. Vite is not in that image.

**Port 5173 is only the frontend dev server.** `npm run dev` in `web/public` starts Vite there and proxies `/api` to `http://127.0.0.1:8770`. It was used to preview page edits without rebuilding the image. It is not the production site, and it does nothing useful unless the API on 8770 is up.

To restart the local container site:

```bash
open -a Docker
cd /Users/kenjikahara/splatoon3-ai-coach
docker compose up -d --build app
```

Then open `http://127.0.0.1:8770`. The `app` service starts the database as well. Source changes under `web/public/src` do not appear in that container until you rebuild the frontend and the image:

```bash
cd /Users/kenjikahara/splatoon3-ai-coach/web/public
npm run build
cd /Users/kenjikahara/splatoon3-ai-coach
docker compose up -d --build app
```


```bash
cd /Users/kenjikahara/splatoon3-ai-coach/web/public
npm run dev
```



Yes. Production has its own file at /srv/www/s3-coaching/.env on the server. The deploy leaves that file alone, so the Stripe values you added locally were not copied.

That file does not have PUBLIC_STRIPE_SECRET_KEY or PUBLIC_STRIPE_WEBHOOK_SECRET yet. Add both lines there:

Voucher redemption also needs `PUBLIC_VOUCHER_HMAC_SECRET` in that same file. It is an HMAC key, not a voucher code. Leave it unset only when no voucher will be entered: submissions without a code keep working, and a request that includes a code fails closed. Do not commit the value. See `docs/public_site/VOUCHERS.md`.

To issue or check vouchers in production, run `docker compose exec app s3-coach public-admin voucher issue|status|list` from that directory; `docs/public_site/VOUCHERS.md` has the exact commands.


ssh Hetzner
cd /srv/www/s3-coaching
nano .env
Then recreate the app container so it reads the file again:


docker compose up -d --force-recreate --no-deps app
A restart is not enough. Compose only loads .env when the container is created.
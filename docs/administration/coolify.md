# Deploy with Coolify

[Coolify](https://coolify.io/docs/) deploys Docker Compose files, but the example
`compose.yaml` of the repository is written for plain Docker Compose with a
reverse proxy on the host: it reads `deploy/production.env`, publishes port 8000
on the host and fixes the range of its network. Under Coolify, these break the
deployment or the trust in Coolify's proxy. The repository therefore has a
second file for Coolify,
[`deploy/coolify/docker-compose.yaml`](https://github.com/HouseCairn/housecairn/blob/main/deploy/coolify/docker-compose.yaml):

- the same image, version and digest, and the same hardening as `compose.yaml`
  (a test of the repository fails when they differ);
- no published port: Coolify's proxy reaches the container over the
  resource's network;
- no `env_file`: every setting is an environment variable that Coolify shows in
  its interface;
- the domain comes from the variables Coolify generates (`SERVICE_URL_HOUSECAIRN`,
  `SERVICE_FQDN_HOUSECAIRN`), so `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and
  `HOUSECAIRN_SITE_URL` follow the domain set in Coolify.

## Create the resource

From the public repository (the file then follows the repository):

1. In a project, **+ New**, then **Public Repository**, and paste
   `https://github.com/HouseCairn/housecairn`. Branch: `main`.
2. **Build Pack**: **Docker Compose**. **Base Directory**: `/deploy/coolify`.
   **Docker Compose Location**: `/docker-compose.yaml`.
3. Save: Coolify reads the file and lists the `housecairn` service and its
   variables.

Or without Git: **+ New**, then **Docker Compose Empty**, and paste the content
of `deploy/coolify/docker-compose.yaml`. You then update the image yourself
(see [Update](#update)).

Leave **Raw (deploy file as-is)** off: Coolify must add its proxy labels and
its network.

## Domain

In **Domains**, give the `housecairn` service one domain, for example
`https://housecairn.example.com`. The file declares `SERVICE_URL_HOUSECAIRN_8000`:
Coolify routes the domain to port 8000 of the container, gets the certificate,
and passes the domain to HouseCairn:

| Variable | Value |
| --- | --- |
| `ALLOWED_HOSTS` | `${SERVICE_FQDN_HOUSECAIRN}`: `housecairn.example.com` |
| `CSRF_TRUSTED_ORIGINS` | `${SERVICE_URL_HOUSECAIRN}`: `https://housecairn.example.com` |
| `HOUSECAIRN_SITE_URL` | `${SERVICE_URL_HOUSECAIRN}`: the address in emails and on printed labels |

Keep one domain: `HOUSECAIRN_SITE_URL` holds a single address.

## Variables

The file sets what does not change under Coolify: `DEBUG=false`,
`HOUSECAIRN_BEHIND_TLS_PROXY=true` (Coolify's proxy terminates HTTPS and sets
`X-Forwarded-Proto`) and `HOUSECAIRN_DATA_DIR=/data`. Coolify lists the others
under **Environment Variables**, with the default of the file. None is required:
HouseCairn starts with all of them as they are.

| Variable | Default in the file | See |
| --- | --- | --- |
| `HOUSECAIRN_FORWARDED_ALLOW_IPS` | `172.16.0.0/12` | [Trusted proxy](#trusted-proxy) below |
| `TIME_ZONE`, `HOUSECAIRN_DEFAULT_LANGUAGE`, `HOUSECAIRN_INSTANCE_NAME` | empty: the setting of the interface | [Environment variables](variables.md) |
| `HOUSECAIRN_PHOTO_MAX_BYTES`, `HOUSECAIRN_HOME_STORAGE_MB`, `HOUSECAIRN_HOME_MAX_FILES` | empty: the setting of the interface | [Environment variables](variables.md) |
| `HOUSECAIRN_SIGNUP_OPEN` | empty: closed | [Open sign-up](sign-up.md) |
| `HOUSECAIRN_SIGNUP_POW_BITS`, `HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS`, `HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS`, `ACCOUNT_EMAIL_VERIFICATION`, `HOUSECAIRN_INVITATION_LIFETIME_DAYS` | empty: the default of the application | [Open sign-up](sign-up.md) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_TIMEOUT` | empty: email off | [Email](email.md) |
| `OIDC_SERVER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `HOUSECAIRN_OIDC_SIGNUP`, `HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL` | empty: single sign-on off | [Single sign-on](oidc.md) |
| `HOUSECAIRN_SETUP_SCREEN` | `true`: the first-account screen is on | [First start](../getting-started/first-start.md) |
| `HOUSECAIRN_NOTIFICATIONS_ENABLED`, `HOUSECAIRN_NOTIFICATIONS_INTERVAL`, `HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS`, `HOUSECAIRN_WEBPUSH_ENABLED`, `HOUSECAIRN_WEBPUSH_DISCREET` | empty: notifications off | [Notifications](../guides/notifications.md) |
| `HOUSECAIRN_LINK_PREVIEW_ENABLED` | empty: off | [Projects](../guides/projects.md) |
| `HOUSECAIRN_BARCODE_CATALOGUE_ENABLED`, `HOUSECAIRN_BARCODE_LOOKUP_ONLINE` | empty: off | [Barcodes](barcodes.md) |
| `HOUSECAIRN_DEMO` | empty: a normal instance | [Demonstration instance](demo.md) |
| `HOUSECAIRN_RATELIMIT_*` (eleven variables) | empty: the default limits | [Rate limits](rate-limits.md) |
| `HOUSECAIRN_CACHE_URL` | `locmemcache://housecairn` | [Rate limits](rate-limits.md) |
| `HOUSECAIRN_URL_PREFIX`, `HOUSECAIRN_URL_PREFIX_HEADER`, `HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING` | empty: off | [Serving under a path prefix](url-prefix.md) |
| `HOUSECAIRN_DISALLOWED_HOST_HINT` | empty: no sentence | [Settings](settings.md#the-page-of-a-refused-host-name) |
| `HOUSECAIRN_DOCS_URL` | `https://docs.housecairn.app` | [Environment variables](variables.md) |
| `HOUSECAIRN_WEB_WORKERS`, `HOUSECAIRN_TASK_WORKERS` | `2`, `2` | [Environment variables](variables.md) |
| `HOUSECAIRN_STOP_TIMEOUT` | empty: 20 seconds | [Environment variables](variables.md) |
| `LOG_LEVEL` | `INFO` | [Environment variables](variables.md) |

Four variables of [Environment variables](variables.md) are not in the file. `SECRET_KEY`
and `SECRET_KEY_FALLBACKS` are secrets: leave them out, and the container generates the
key on first start and keeps it in `/data`. `DATABASE_URL` is set in the
[PostgreSQL](#postgresql) section, with the password Coolify generates.
`HOUSECAIRN_PORT` stays at 8000, the port Coolify routes the domain to. A test of the
repository fails when a variable of that page is in neither the file nor this list.

For a demonstration instance, set `HOUSECAIRN_DEMO` to `true` and leave
`HOUSECAIRN_SIGNUP_OPEN` empty: two public accounts, settings read only, nothing sent,
everything erased every night. Never on an instance with real data
([Demonstration instance](demo.md)).

A setting that the interface can also change (time zone, sign-up, email, limits) stays
free in the interface while its variable is empty; once you fill it in Coolify,
it locks its field there ([Settings](settings.md)).

## Trusted proxy

HouseCairn trusts the `X-Forwarded-Proto` and `X-Forwarded-For` headers only
from the addresses in `HOUSECAIRN_FORWARDED_ALLOW_IPS`
([Reverse proxy](reverse-proxy.md#trusted-proxy-address)). Coolify's proxy
connects from its address on the resource's network, which Docker chooses. The
default, `172.16.0.0/12`, is the range Docker gives its networks: it covers the
proxy on most servers, and the container publishes no port, so only containers
on the same network can reach it.

To narrow it to the network of the resource, on the server:

```sh
docker ps --filter name=housecairn --format '{{.Names}}'
docker inspect <container name> \
  --format '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}'
docker network inspect <network name> --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}'
```

Set `HOUSECAIRN_FORWARDED_ALLOW_IPS` to that subnet, for example
`172.18.0.0/16`, then redeploy. If the subnet is outside `172.16.0.0/12` (a
server with many networks, or a Docker daemon with its own address pools), the
default does not cover the proxy: every request is redirected to HTTPS again
and the browser reports too many redirects. Set the variable to the real
subnet.

## Data and backups

Everything HouseCairn writes is in the `housecairn-data` volume, mounted on
`/data`. Coolify prefixes its name with the resource's identifier; **Persistent
Storage** shows it. Back it up as described in [Data and backups](backup.md):
the commands run in the container from Coolify's **Terminal**, without
`docker compose exec housecairn`, for example
`python -m housecairn.entrypoint manage backup_instance`.

On first start, HouseCairn writes a one-time code in its log while it has no
administrator: read it under **Logs**, then
[create the first administrator](../getting-started/first-start.md).

## PostgreSQL

SQLite in `/data` is the default and is enough for a household. For PostgreSQL,
use a **Docker Compose Empty** resource, paste the file, then add a `postgres`
service and point HouseCairn at it. Coolify generates the password
(`SERVICE_PASSWORD_POSTGRES`, letters and digits only, so it fits in the URL):

```yaml
services:
  housecairn:
    # ... the service of deploy/coolify/docker-compose.yaml, plus:
    environment:
      - DATABASE_URL=postgres://housecairn:${SERVICE_PASSWORD_POSTGRES}@postgres:5432/housecairn
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    # The `image:` line of the `postgres` service of compose.yaml.
    image: postgres:<version>@sha256:<digest>
    restart: unless-stopped
    environment:
      - POSTGRES_USER=housecairn
      - POSTGRES_DB=housecairn
      - POSTGRES_PASSWORD=${SERVICE_PASSWORD_POSTGRES}
    volumes:
      - housecairn-postgres:/var/lib/postgresql
    security_opt: ["no-new-privileges:true"]
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "housecairn", "-d", "housecairn"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  housecairn-data:
  housecairn-postgres:
```

Back up the database with `pg_dump` in the `postgres` container, as in
[With PostgreSQL](backup.md#with-postgresql).

## Update

The file names the latest released version and its digest, changed together
with `compose.yaml`. From the repository, a new deployment in Coolify reads the
file again and takes the new image. Read the [release notes](../release-notes.md)
and [back up](backup.md) first, as in [Update](update.md). With **Docker Compose
Empty**, change the image line yourself, and
[verify the image](../security/verify-image.md).

## Pitfalls

- **`pull_policy: missing`**: the file pins a digest, which cannot move, so
  Coolify pulls the image only when the server does not have it. If you replace
  it with a moving tag (`edge`, `latest`, `X.Y`), set `pull_policy: always`, or
  each deployment keeps the image already on the server.
- **Too many redirects**: `HOUSECAIRN_FORWARDED_ALLOW_IPS` does not cover the
  proxy's address (see [Trusted proxy](#trusted-proxy)).
- **Bad Request (400)**: the request's host name is not in `ALLOWED_HOSTS`. Set
  the domain in **Domains**, and redeploy so the variables follow it.
- **No published port**: do not add `ports:` to reach the container directly.
  A published port bypasses the proxy, and two instances on one server would
  ask for the same port.

This page follows Coolify's documentation of Docker Compose. The file is
validated by the repository's CI (`docker compose config` with the variables
Coolify sets), but not by a deployment on a real Coolify server: try it on a
test instance before you rely on it.

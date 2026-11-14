# Reverse proxy

HouseCairn listens on plain HTTP and expects a reverse proxy to terminate
HTTPS in front of it. With the example `compose.yaml`, it listens on
`127.0.0.1:8000` of the host.

In `deploy/production.env`, for an instance at `housecairn.example.com`:

```ini
ALLOWED_HOSTS=housecairn.example.com
CSRF_TRUSTED_ORIGINS=https://housecairn.example.com
HOUSECAIRN_BEHIND_TLS_PROXY=true
HOUSECAIRN_FORWARDED_ALLOW_IPS=192.0.2.1
```

`HOUSECAIRN_BEHIND_TLS_PROXY=true` makes HouseCairn trust the
`X-Forwarded-Proto` header: set it only when the proxy sets that header itself.
Without it, HouseCairn sees plain HTTP and redirects every request to HTTPS.

## Trusted proxy address

`HOUSECAIRN_FORWARDED_ALLOW_IPS` names the proxy: one or more IP addresses or
networks, comma-separated (`192.0.2.1`, `192.0.2.0/24`, `2001:db8::/32`). The
default is the container's own loopback, `127.0.0.1,::1`.

- From these addresses, `X-Forwarded-Proto` tells HouseCairn that the request
  came in over HTTPS, and `X-Forwarded-For` gives the client's address, used
  to limit sign-in attempts per client.
- From any other address, HouseCairn removes the forwarding headers
  (`X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `Forwarded`,
  `X-Real-IP`...) before reading the request. A client that reaches the
  container without going through the proxy can neither pass for HTTPS nor
  choose the address its sign-in attempts are counted under.

The value is the address HouseCairn sees the proxy connect from, not the
proxy's public address:

| Where the proxy runs | Value |
| --- | --- |
| On the host, with the example `compose.yaml` (port published on `127.0.0.1`) | The gateway of the Compose network: `192.0.2.1` with the example's fixed network. |
| In a container of the same Compose project | That container's address, fixed in `compose.yaml` (example below). |
| On another machine | That machine's address. |

If the value is wrong, HouseCairn ignores the proxy's headers: every request is
redirected to HTTPS again (the browser reports too many redirects), and every
client is counted under the proxy's address. `*` trusts any peer; use it only if
nothing but the proxy can reach the container's port.

HouseCairn reads the site's name from the `Host` header, never from
`X-Forwarded-Host`: the proxy must pass `Host` through. Caddy and Traefik do
by default; nginx needs `proxy_set_header Host $host`.

A proxy may replace HouseCairn's `Referrer-Policy: same-origin` header, as Home
Assistant does with `no-referrer`. Each page also states the policy in its
`<head>`, which the browser follows over the header: forms keep sending their
origin, which the CSRF check needs ([ADR 0076](../adr/0076-referrer-policy-in-the-page.md)).

## Caddy on the host

Caddy obtains the certificate and sets `X-Forwarded-Proto` and
`X-Forwarded-For` by default:

```text
housecairn.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Keep `HOUSECAIRN_FORWARDED_ALLOW_IPS=192.0.2.1`, the gateway of the example
network. If you change the network's range in `compose.yaml`, change this
value with it.

## Caddy or Traefik in Docker Compose

The proxy runs next to HouseCairn, with a fixed address on a network that only
the two of them share. HouseCairn publishes no port: only the proxy does.

```yaml
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
    networks:
      proxy:
        ipv4_address: 192.0.2.10

  housecairn:
    image: ghcr.io/housecairn/housecairn:X.Y.Z
    restart: unless-stopped
    env_file: deploy/production.env
    environment:
      HOUSECAIRN_FORWARDED_ALLOW_IPS: 192.0.2.10
    volumes:
      - housecairn-data:/data
    networks: [proxy]
    # Plus the hardening options of the example compose.yaml: read_only, tmpfs,
    # cap_drop, security_opt, stop_grace_period.

networks:
  proxy:
    ipam:
      config:
        # A documentation range (RFC 5737): any free range works.
        - subnet: 192.0.2.0/24

volumes:
  housecairn-data:
  caddy-data:
```

With the `Caddyfile`:

```text
housecairn.example.com {
    reverse_proxy housecairn:8000
}
```

With Traefik instead of Caddy, the setting is the same: give the Traefik
container a fixed address on the shared network and set
`HOUSECAIRN_FORWARDED_ALLOW_IPS` to it. Traefik sets `X-Forwarded-Proto` and
`X-Forwarded-For`, and replaces those a client sends, by default. The labels of
the `housecairn` service:

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.housecairn.rule=Host(`housecairn.example.com`)
      - traefik.http.routers.housecairn.entrypoints=websecure
      - traefik.http.routers.housecairn.tls.certresolver=letsencrypt
      - traefik.http.services.housecairn.loadbalancer.server.port=8000
```

## nginx

```nginx
server {
    listen 443 ssl;
    server_name housecairn.example.com;
    # ssl_certificate and ssl_certificate_key of your certificate.

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

`$proxy_add_x_forwarded_for` keeps what the client sent and appends the
address nginx saw: HouseCairn reads the list from the right and stops at the
first address that is not a trusted proxy, so the client's own entries do not
count.

HouseCairn sets its own security headers (Content Security Policy, HSTS,
`X-Frame-Options`, and a `Permissions-Policy` that switches off camera, microphone,
location, payment and USB); the proxy does not need to add them.

## Logs and the calendar feed address

The address of a [calendar feed](../guides/calendar.md), `/calendar/feed/<secret>.ics`, is a
secret that opens the feed. HouseCairn writes it as `/calendar/feed/***.ics` in its own access
log and warnings, but the proxy sees the whole path and writes it in its access log unless told
otherwise. With nginx, switch the access log off for that path:

```nginx
    location /calendar/feed/ {
        access_log off;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
```

Caddy (the `log` directive and its `filter` encoder) and Traefik (`accessLog.fields.names`,
`RequestPath` and `RequestLine`) can drop or rewrite the same fields; check their documentation
for your version, the project has not tried them. A log that has already kept an address is a
leak: regenerate the address.

## Behind an authentication proxy

When a proxy asks for a sign-in before HouseCairn (forward authentication), browsers would
fetch the web app manifest without the cookie of that sign-in, get the proxy's sign-in page
instead, and offer no installation on a phone. HouseCairn asks the browser to send the
cookie with the manifest (`crossorigin="use-credentials"`), so nothing needs to change in
the proxy. If the proxy lets some paths through without a sign-in, the manifest
(`/manifest.webmanifest`) and the service worker (`/service-worker.js`) do not need to be
among them.

The service worker keeps Today and the task list of the person for reading offline
([Install on a phone](../getting-started/phone.md)). Whatever the proxy, it never keeps a
page whose answer is `Cache-Control: no-store`: a proxy that adds that header to every page
turns offline reading off.

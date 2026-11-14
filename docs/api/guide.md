# Using the API

A first tour of the API with `curl`: a token, a few reads, a few writes, the pages of a
list and the errors. Every command of this page is run against a real instance by the
tests of HouseCairn, at the root of a host and under a path prefix
(`tests/test_api_guide.py`): they work as written.

## Before you start

1. **Create a token** on the [API tokens](../screens/api-tokens.md) screen: a name, the
   homes it may use, its scopes (`read` to read, `write` to change), its lifetime. The
   page shows the token **once**, right after you create it: copy it then; it is not
   kept and cannot be shown again.
2. **Note the address of your instance**, with its path prefix if it has one
   ([Serving under a path prefix](../administration/url-prefix.md)), and the identifier
   of a home, the UUID in the address of its page, `/h/<home id>/`.

The examples read them from three variables of the shell:

```text
export HC=https://housecairn.example.com          # or https://example.com/housecairn
export TOKEN=hc_...                               # the token, as the screen showed it
export HOME_ID=0b6a4c1e-...                       # the identifier of a home
```

Keep the token out of your shell history and of shared scripts: it acts with your rights
on its homes until it expires or you revoke it.

## Authentication

Send the token in the `Authorization` header, as a bearer token, on every request:

```sh
curl -fsS -H "Authorization: Bearer $TOKEN" "$HC/api/v1/homes/"
```

The answer lists the homes the token may use, with your role in each. A request without
a token, or with a token that is wrong, expired or revoked, gets `401`:

```sh
curl -s -o /dev/null -w '%{http_code}\n' "$HC/api/v1/homes/"
# prints: 401
```

A token is bound to its homes: another home is a `404`, as if it did not exist. Its
scopes decide what it may do: `read` for `GET`, `write` for every other method. A
token never manages tokens, the household or the account: those are for a person signed
in ([API](index.md)).

## Reading

The rooms of a home, then its tasks that are still open:

```sh
curl -fsS -H "Authorization: Bearer $TOKEN" "$HC/api/v1/homes/$HOME_ID/rooms/"
curl -fsS -H "Authorization: Bearer $TOKEN" "$HC/api/v1/homes/$HOME_ID/tasks/?status=open"
```

A table of the home as CSV, for a spreadsheet. The address ends with a `/` like every
other: without it the answer is a redirect, and `curl` writes an empty file. The first line
of the file names the columns:

```sh
curl -fsS -H "Authorization: Bearer $TOKEN" -o rooms.csv \
  "$HC/api/v1/homes/$HOME_ID/export/rooms.csv/"
head -n 1 rooms.csv | tr , "\n" | grep -x name
# prints: name
```

## Writing

Requests that change something send JSON, with the `write` scope. A task needs only its
title:

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Bleed the radiators", "due_on": "2026-12-01"}' \
  "$HC/api/v1/homes/$HOME_ID/tasks/"
```

The answer, `201`, is the task, with its `id`. A change is a `PATCH` of the fields that
change, at the address of the task, `.../tasks/<id>/`; `PUT` is not offered.

A task made from a report lists it in `reports`: for each, `id`, `created_at`, `has_photo`
and `photo_url`, the protected address of the photo, fetched with the same token. A token
of a read-only member sees the reports that member made and no others.

Common tasks come ready from the [library of task templates](../screens/task-templates.md):
list it, then add some of its templates to a home, in the language of the request:

```sh
curl -fsS -H "Authorization: Bearer $TOKEN" "$HC/api/v1/task-templates/"
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Accept-Language: fr" \
  -d '{"templates": ["smoke-alarm-test", "smoke-alarm-battery"]}' \
  "$HC/api/v1/homes/$HOME_ID/tasks/from-templates/"
```

## Pages of a list

A list comes in pages, from the most recent: `results` holds the rows, `next` and
`previous` the addresses of the next and the previous pages, or `null`. `page_size`
asks for up to 200 rows a page (50 by default). The addresses are cursors: follow them as
they are, rather than build them; they stay right while rows are added. To read every
page:

```sh
url="$HC/api/v1/homes/$HOME_ID/tasks/?page_size=2"
while [ -n "$url" ]; do
  page=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$url")
  printf '%s\n' "$page"
  url=$(printf '%s' "$page" | python3 -c 'import json, sys; print(json.load(sys.stdin)["next"] or "")')
done
```

## Errors

An error answers with a code and a JSON body that says why: `{"detail": "..."}`, or, for a
value refused, the name of each field with its messages, `{"title": ["..."]}`.

| Code | When |
| --- | --- |
| `400` | A value is missing or refused; the body names the field. |
| `401` | No token, or a token that is wrong, expired or revoked. |
| `403` | The token lacks the scope, your role does not allow it (a read-only member that writes), or the route is for a person signed in. A home that requires a sign-in provider answers `403` with `"code": "sso_required"` and the provider, until the session signs in through it. |
| `404` | Nothing at this address for you: an object of another home, or a home the token is not bound to, is a `404` and never a `403`, so that its existence does not show. |
| `405` | A method the route does not offer (`PUT`, say). |
| `429` | Too many attempts of a limited action, such as sending invitations; try again later. |

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"title": ""}' "$HC/api/v1/homes/$HOME_ID/tasks/"
# prints: 400
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  "$HC/api/v1/homes/00000000-0000-0000-0000-000000000000/rooms/"
# prints: 404
```

## Trying it in the browser

A running instance serves its OpenAPI schema at `/api/schema/` and an interactive explorer
at `/api/docs/`, under its path prefix, to a person signed in: every operation, its
parameters and its answers, which you can try with your session. The
[reference](reference.md) gives the same operations, generated from the code of this
version.

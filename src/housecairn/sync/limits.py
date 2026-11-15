"""The limits the server announces in `GET /api/v1/sync/info` (docs/adr/0077, need 11)."""

from housecairn.instance import ratelimits
from housecairn.sync import pushing

READ_ACTION = "housecairn_sync_read"
SNAPSHOT_ACTION = "housecairn_sync_snapshot"
PUSH_ACTION = "housecairn_sync_push"


def per_minute(action: str) -> int:
    """The amount per minute of a rate of ADR 0042 (`<amount>/m/key`)."""
    return int(ratelimits.rates()[action].split("/", 1)[0])


def announced() -> dict[str, int]:
    """What a phone must stay under: reads, snapshots and pushes per minute per connection."""
    return {
        "reads_per_minute": per_minute(READ_ACTION),
        "snapshots_per_minute": per_minute(SNAPSHOT_ACTION),
        "pushes_per_minute": per_minute(PUSH_ACTION),
        "ops_per_push": pushing.MAX_OPS,
        "push_bytes": pushing.MAX_BODY_BYTES,
    }

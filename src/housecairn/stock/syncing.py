"""What of an article, a supplier and a movement the sync API sends (docs/adr/0077, 0079).

The fields of the protocol are the columns of the models, `created_at` included; a label is
sent by the name of the stored file until the lot of the files (M6) maps it to the reference
of ADR 0077. Not sent: the columns derived from the label (its type) and the effect of a
movement (`delta`, derived on read).
"""

from housecairn.stock.models import Article, Movement, Supplier
from housecairn.sync import rows

CREATED = frozenset({"created_at"})


def register() -> None:
    rows.register(
        Article,
        rows.Spec(
            skip=frozenset({"label_content_type"}), send=CREATED, file_names=frozenset({"label"})
        ),
    )
    rows.register(Supplier, rows.Spec(send=CREATED))
    rows.register(Movement, rows.Spec(skip=frozenset({"delta"}), send=CREATED))

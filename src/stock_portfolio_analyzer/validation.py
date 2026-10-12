"""Duplicate and inventory validation. Cost-basis calculations come later."""

from collections import defaultdict
from decimal import Decimal
from typing import Collection, Sequence

from .models import Issue, Transaction, ValidationResult


def validate_transactions(incoming: Sequence[Transaction], *,
                          existing: Sequence[Transaction] = (),
                          include_duplicates: Collection[str] = ()) -> ValidationResult:
    """Review a batch without mutating either it or the accepted session history.

    Dates determine inventory order. Ties retain existing import order, then
    incoming file/row order. Potential duplicates consume no inventory unless
    explicitly included. A batch that invalidates existing history is blocked.
    """
    result = ValidationResult()
    seen_fingerprints: dict[tuple, Transaction] = {}
    seen_ids: dict[tuple, Transaction] = {}
    candidates: list[tuple[int, Transaction]] = []
    for index, tx in enumerate([*existing, *incoming]):
        is_existing = index < len(existing)
        tx_id = (tx.brokerage, tx.transaction_id) if tx.transaction_id else None
        same_id = seen_ids.get(tx_id) if tx_id else None
        same_economics = seen_fingerprints.get(tx.fingerprint)
        # Held rows still establish an ID's meaning. Otherwise a later row could
        # reuse their ID with different economics without surfacing a conflict.
        if tx_id:
            seen_ids.setdefault(tx_id, tx)
        if not is_existing:
            if same_id and same_id.fingerprint != tx.fingerprint:
                result.rejected.append(Issue.for_transaction(tx, "conflicting transaction ID within this account; correct the source record."))
                continue
            duplicate = same_id or same_economics
            if duplicate and tx.row_key not in include_duplicates:
                result.duplicates.append(Issue.for_transaction(
                    tx, f"Potential duplicate of {duplicate.source}, record {duplicate.source_row}. "
                    "Held until explicitly included or excluded."
                ))
                continue
        seen_fingerprints.setdefault(tx.fingerprint, tx)
        candidates.append((index, tx))

    quantities: dict[tuple, Decimal] = defaultdict(lambda: Decimal(0))
    accepted_indices = set()
    for index, tx in sorted(candidates, key=lambda item: (item[1].date, item[0])):
        available = quantities[tx.inventory_key]
        if tx.action == "SELL" and tx.quantity > available:
            reason = f"Sale exceeds available quantity {available} in this account, exchange, ticker, and currency. Check for incomplete history."
            if index < len(existing):
                result.blockers.append(f"Import would invalidate existing {tx.source}, record {tx.source_row}: {reason}")
            else:
                result.rejected.append(Issue.for_transaction(tx, reason))
            continue
        quantities[tx.inventory_key] += tx.quantity if tx.action == "BUY" else -tx.quantity
        if index >= len(existing):
            accepted_indices.add(index)
    result.accepted = [tx for index, tx in candidates if index in accepted_indices]
    return result

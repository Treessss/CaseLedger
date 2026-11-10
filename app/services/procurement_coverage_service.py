"""Procurement promises are stable; physical stock is still shared at scan time."""

from collections import Counter


def forecast_coverage(demands, incoming, physical, promised, accepted, choices, preferred=()):
    """Cover each demand once, conserving every physical and in-transit unit.

    Existing purchase allocations get first refusal, not warehouse reservations.
    Unbound/excess supply remains shared. Damage/missing quantities must already
    be excluded from promised; accepted units are capped by actual physical stock.
    """
    remaining = [Counter(demand) for demand in demands]
    pending = [Counter() for _ in demands]
    transit = Counter(incoming)
    stock = Counter(physical)

    def owned_supply(owners, budget, *, in_transit):
        for index, coverage in enumerate(owners):
            for key, quantity in coverage.items():
                take = min(max(quantity, 0), max(budget[key], 0), max(remaining[index][key], 0))
                if take:
                    budget[key] -= take
                    remaining[index][key] -= take
                    if in_transit:
                        pending[index][key] += take

    # Otherwise an older/new original can steal a CF promise in the forecast,
    # even while its first purchase is still in transit, and buy it again.
    owned_supply(promised, transit, in_transit=True)
    owned_supply(accepted, stock, in_transit=False)
    # Preserve the whole-package forecast where it does not conflict with an
    # existing procurement promise. Incomplete originals must not strand a CF
    # that can actually ship from the shared stock right now.
    for index, lines in enumerate(preferred):
        for line in lines:
            source, target = line['group_key'], line.get('target_key') or line['group_key']
            take = min(max(int(line['quantity']), 0), max(stock[source], 0), max(remaining[index][target], 0))
            stock[source] -= take
            remaining[index][target] -= take

    def shared_supply(budget, *, in_transit):
        for compatible in (False, True):
            for index, demand in enumerate(remaining):
                for target in demand:
                    sources = choices(target)[1:] if compatible else (target,)
                    for source in sources:
                        take = min(max(demand[target], 0), max(budget[source], 0))
                        if take:
                            demand[target] -= take
                            budget[source] -= take
                            if in_transit:
                                pending[index][target] += take
                        if not demand[target]:
                            break

    shared_supply(stock, in_transit=False)
    shared_supply(transit, in_transit=True)
    return remaining, pending

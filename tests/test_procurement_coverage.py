"""Purchase ownership must not turn into physical warehouse reservations."""
from collections import Counter

from app.services.procurement_coverage_service import forecast_coverage


def plan(demands, incoming=None, stock=None, promised=None, accepted=None, choices=None):
    return forecast_coverage([Counter(d) for d in demands], Counter(incoming or {}), Counter(stock or {}),
        [Counter(d) for d in (promised or [{} for _ in demands])],
        [Counter(d) for d in (accepted or [{} for _ in demands])], choices or (lambda key: [key]))


def test_cf_promise_is_not_reassigned_to_new_originals():
    missing, pending = plan([{'x': 1}, {'x': 1}, {'x': 1}], incoming={'x': 2},
                            promised=[{}, {}, {'x': 2}])
    assert [d['x'] for d in missing] == [0, 1, 0]
    assert [d['x'] for d in pending] == [1, 0, 1]


def test_owner_incoming_does_not_also_consume_physical_stock():
    missing, pending = plan([{'x': 1}, {'x': 1}], incoming={'x': 1}, stock={'x': 1},
                            promised=[{'x': 1}, {}])
    assert not any(sum(d.values()) for d in missing)
    assert [d['x'] for d in pending] == [1, 0]


def test_13250_three_products_remain_covered_after_priority_changes():
    missing, _ = plan([{'red': 1}, {'blue': 1, 'red': 1, 'green': 1}],
        incoming={'blue': 1, 'red': 1, 'green': 1},
        promised=[{}, {'blue': 1, 'red': 1, 'green': 1}])
    assert missing[0]['red'] == 1 and sum(missing[1].values()) == 0


def test_accepted_goods_prefer_original_procurement_but_only_if_still_in_stock():
    missing, _ = plan([{'x': 1}, {'x': 1}], stock={'x': 1}, accepted=[{}, {'x': 1}])
    assert [d['x'] for d in missing] == [1, 0]
    missing, _ = plan([{'x': 1}, {'x': 1}], accepted=[{}, {'x': 1}])
    assert [d['x'] for d in missing] == [1, 1]  # Real shipment/count loss reopens demand.


def test_cancelled_or_changed_demand_releases_surplus():
    missing, pending = plan([{'y': 1}, {'x': 2}], incoming={'x': 3, 'y': 1},
                            promised=[{'x': 2}, {}])
    assert not any(sum(d.values()) for d in missing)
    assert sum(sum(d.values()) for d in pending) == 3


def test_exact_model_promises_before_compatible_surplus():
    def choices(key):
        return [key, '18' if key == '17' else '17']
    missing, _ = plan([{'17': 1}, {'18': 1}], incoming={'18': 1},
                      promised=[{}, {'18': 1}], choices=choices)
    assert missing[0]['17'] == 1 and missing[1]['18'] == 0


def test_unbound_stock_and_incoming_cover_each_demand_once():
    missing, pending = plan([{'x': 2}, {'x': 2}], incoming={'x': 1}, stock={'x': 2})
    assert sum(d['x'] for d in missing) == 1
    assert sum(d['x'] for d in pending) == 1


def test_buy_all_uncovered_is_idempotent_on_next_round():
    demands = [{'x': 2, 'y': 1}, {'x': 1}, {'x': 1, 'y': 1}]
    incoming, stock = Counter({'x': 1}), Counter({'y': 1})
    promised = [Counter(), Counter(), Counter({'x': 1})]
    first, _ = plan(demands, incoming=incoming, stock=stock, promised=promised)
    assert first[2]['x'] == 0
    for index, missing in enumerate(first):
        incoming.update(missing)
        promised[index].update(missing)
    for _ in range(4):
        again, _ = plan(demands, incoming=incoming, stock=stock, promised=promised)
        assert sum(sum(d.values()) for d in again) == 0


def test_supply_conservation_across_random_procurement_histories():
    import random
    randomizer = random.Random(13250)
    for _ in range(100):
        demands = [{'x': randomizer.randrange(5)} for _ in range(8)]
        incoming, stock = randomizer.randrange(12), randomizer.randrange(12)
        promised = [{'x': randomizer.randrange(5)} for _ in demands]
        accepted = [{'x': randomizer.randrange(5)} for _ in demands]
        missing, pending = plan(demands, incoming={'x': incoming}, stock={'x': stock},
                                promised=promised, accepted=accepted)
        shortage = sum(d['x'] for d in missing)
        assert shortage == max(sum(d['x'] for d in demands) - incoming - stock, 0)
        assert sum(d['x'] for d in pending) <= incoming
        assert all(d['x'] >= 0 for d in missing)

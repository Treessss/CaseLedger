"""Identify non-merchandise lines without hiding real or deleted products."""


def is_tip_line(item):
    def value(field):
        return item.get(field) if isinstance(item, dict) else getattr(item, field, None)

    return (
        str(value("title") or "").strip().casefold() in ("tip", "tips")
        and not value("shopify_product_id")
        and not value("shopify_variant_id")
        and not str(value("sku") or "").strip()
        and not str(value("variant_title") or "").strip()
    )

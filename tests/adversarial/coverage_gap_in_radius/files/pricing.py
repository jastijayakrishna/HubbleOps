DISCOUNT_FIELD = "campaigns.name"


def discounted(rows, rate):
    return [(row[DISCOUNT_FIELD], rate) for row in rows]

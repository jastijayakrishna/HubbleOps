from reporting import campaign_identifiers


def total_spend(rows, rate):
    return sum(identifier * rate for identifier in campaign_identifiers(rows))

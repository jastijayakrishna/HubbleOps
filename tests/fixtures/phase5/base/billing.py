from reporting import campaign_identifiers

RETRYABLE_ERRORS = ("MockProvError",)


def total_spend(rows, rate):
    return sum(identifier * rate for identifier in campaign_identifiers(rows))

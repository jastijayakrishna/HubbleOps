from provider_bridge import submit_report


def run_report(account_id: str) -> object:
    return submit_report(account_id, "SELECT campaign.id FROM campaign")

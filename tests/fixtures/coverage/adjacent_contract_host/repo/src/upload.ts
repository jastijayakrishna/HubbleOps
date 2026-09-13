const ADS = "https://googleads.googleapis.com/v22";

export async function readAccounts(accessToken: string) {
  return fetch(`${ADS}/customers:listAccessibleCustomers`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function ingestEvent(accessToken: string, body: unknown) {
  return fetch("https://datamanager.googleapis.com/v1/events:ingest", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify(body),
  });
}

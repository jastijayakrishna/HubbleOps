const ENDPOINT = "https://googleads.googleapis.com/v22";

export async function listConversionActions(customerId: string) {
  const query =
    "SELECT conversion_action.id, conversion_action.name FROM conversion_action WHERE conversion_action.status = ENABLED";
  return post(`${ENDPOINT}/customers/${customerId}/googleAds:searchStream`, query);
}

export async function listFutureResource(customerId: string) {
  const query = "SELECT brand_guidelines.id FROM brand_guidelines LIMIT 1";
  return post(`${ENDPOINT}/customers/${customerId}/googleAds:searchStream`, query);
}

async function post(url: string, query: string) {
  return fetch(url, { method: "POST", body: JSON.stringify({ query }) });
}

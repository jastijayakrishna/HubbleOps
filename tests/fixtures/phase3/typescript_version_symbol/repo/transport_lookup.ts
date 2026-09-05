import { protos } from "google-ads-node";

import { API_RELEASE } from "./release_value";

export const serviceTypes = protos.google.ads.googleads[API_RELEASE].services;

export function endpointFor(accountId: string): string {
  return `https://googleads.googleapis.com/${API_RELEASE}/customers/${accountId}/googleAds:search`;
}

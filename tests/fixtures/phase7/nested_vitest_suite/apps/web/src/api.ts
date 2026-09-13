import { GOOGLE_ADS_API_VERSION, GOOGLE_ADS_ENDPOINT } from "./constants";

export function searchUrl(customerId: string): string {
  return `${GOOGLE_ADS_ENDPOINT}/customers/${customerId}/googleAds:search`;
}

export function apiVersion(): string {
  return GOOGLE_ADS_API_VERSION;
}

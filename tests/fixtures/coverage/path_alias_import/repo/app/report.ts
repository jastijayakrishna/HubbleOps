import { endpoint } from "@/lib/ads/client";

export function reportUrl(customerId: string) {
  return endpoint(`customers/${customerId}/googleAds:searchStream`);
}

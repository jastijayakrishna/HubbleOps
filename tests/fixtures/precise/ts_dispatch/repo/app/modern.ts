import { AdsTransport } from "@/lib/transport";

export function direct() {
  const client = new AdsTransport();
  return client.send("customers/1/googleAds:search", "v25");
}

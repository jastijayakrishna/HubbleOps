import type { Transport } from "@/lib/port";

export function viaInterface(port: Transport) {
  return port.send("customers/1/googleAds:search", "v22");
}

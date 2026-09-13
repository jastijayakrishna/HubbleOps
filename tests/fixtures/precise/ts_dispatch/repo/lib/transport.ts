import type { Transport } from "./port";

export class AdsTransport implements Transport {
  send(path: string, version: string) {
    return fetch(`https://googleads.googleapis.com/${version}/${path}`);
  }
}

export function deliver(path: string, version: string) {
  return fetch(`https://googleads.googleapis.com/${version}/${path}`);
}

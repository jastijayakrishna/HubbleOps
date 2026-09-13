import { endpoint } from "../lib/google-ads/client";

export function SettingsLink({ customerId }: { customerId: string }) {
  return <a href={endpoint(customerId)}>Open settings</a>;
}

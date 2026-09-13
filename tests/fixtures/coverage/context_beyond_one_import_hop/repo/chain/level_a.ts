import { base } from "./level_b";

export function url(customerId: string) {
  return `${base()}/customers/${customerId}/conversionActions:mutate`;
}
